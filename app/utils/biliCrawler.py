#暂时不用
"""
封装 bilibili-api 的 get_dynamic_page_list，和一个简单的 UP 主监控器。

提供函数：
 - async def get_dynamic_page_list(...)
 - async def monitor_up(host_mid, ...)

使用示例（在模块底部有一个简单的 demo）：
	python biliCrawler.py

注意：此模块依赖于仓库中的 bilibili-api-python 包（requirements.txt 已包含）。
"""

from typing import Optional, List, Callable, Any
import asyncio
import logging
import json
import os
from pathlib import Path

from bilibili_api import Credential
from bilibili_api.dynamic import get_dynamic_page_list as api_get_dynamic_page_list
from bilibili_api.dynamic import Dynamic
from bilibili_api.dynamic import DynamicType

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def make_credential() -> Credential:
	"""从环境变量创建 Credential 实例。"""
	credential_values = {
		'sessdata': os.environ.get('BILIBILI_SESSDATA', '').strip(),
		'bili_jct': os.environ.get('BILIBILI_JCT', '').strip(),
		'buvid3': os.environ.get('BILIBILI_BUVID3', '').strip(),
		'dedeuserid': os.environ.get('BILIBILI_DEDEUSERID', '').strip(),
	}
	missing = [name.upper() for name, value in credential_values.items() if not value]
	if missing:
		raise RuntimeError(
			'Missing Bilibili credentials in environment: ' + ', '.join(missing)
		)
	return Credential(**credential_values)


async def get_dynamic_page_list(credential: Optional[Credential] = None,
								_type: DynamicType = DynamicType.ALL,
								host_mid: Optional[int] = None,
								features: str = "itemOpusStyle",
								pn: int = 1,
								offset: Optional[int] = None) -> List[Any]:
	"""异步获取动态页动态列表。

	参数遵循 nemo/bilibili-api 文档：
	- credential: bilibili_api.Credential，如果为 None 则从模块常量构造
	- _type: DynamicType，默认 DynamicType.ALL
	- host_mid: 指定 UP 主的 mid
	- features: 默认为 "itemOpusStyle"
	- pn: 页码，默认 1
	- offset: 偏移值（下一页第一个动态 id）

	返回值：list[Dynamic]
	"""
	if credential is None:
		credential = make_credential()

	dynamics = await api_get_dynamic_page_list(credential=credential,
											   _type=_type,
											   host_mid=host_mid,
											   features=features,
											   pn=pn,
											   offset=offset)
	return dynamics


async def get_dynamic_page_info(dynamic_id: Optional[int],
								credential: Optional[Credential] = None) -> dict:
	"""通过 dynamic id 获取动态详情，返回一个 dict（尽量可序列化）。
	参数:
	  - dynamic_id: 可选的动态 id
	  - credential: 可选的 Credential

	返回: dict 风格的详情数据（如果 API 返回复杂对象，会尝试序列化为 dict）。
	"""
	if credential is None:
		credential = make_credential()

	# 确保 dynamic_id 为整数并使用 Dynamic 实例获取信息
	if dynamic_id is None:
		dynamic_id = 1105123612456648704
	dyn = Dynamic(int(dynamic_id), credential)
	info = await dyn.get_info()

	# info 通常为 dict，直接返回，否则尝试序列化
	if isinstance(info, dict):
		return info
	try:
		serialized = _serialize_dynamic_item(info)
		if isinstance(serialized, dict):
			return serialized
		return {"data": serialized}
	except Exception:
		return {"raw": str(info)}


async def monitor_up(host_mid: int,
					 credential: Optional[Credential] = None,
					 poll_interval: int = 60,
					 on_new: Optional[Callable[[List[Any]], None]] = None,
					 _type: DynamicType = DynamicType.ALL,
					 save_path: Optional[str] = None) -> None:
	"""简单的轮询监控指定 UP 主的动态。

	- host_mid: 要监控的 UP 主 mid
	- credential: 可选的 Credential
	- poll_interval: 轮询间隔（秒）
	- on_new: 当检测到新的动态列表时回调，接收新动态列表作为参数；如果为 None，默认打印
	- _type: 动态类型过滤

	逻辑：每次请求第一页（pn=1），检查 item 的 id（或 desc.uid/desc.dynamic_id），记录已见的最新 id，发现未知 id 则视为新动态并回调。
	"""
	if credential is None:
		credential = make_credential()

	seen_ids = set()

	async def default_on_new(items: List[Any]):
		logger.info("Found %d new dynamics for %s", len(items), host_mid)
		for it in items:
			# 动态对象的具体结构依赖 bilibili-api 的 Dynamic 类型，尽量打印关键字段
			try:
				desc = getattr(it, 'desc', None)
				if desc is not None:
					dyn_id = desc.get('dynamic_id') or desc.get('rid') or desc.get('id')
				else:
					dyn_id = getattr(it, 'dynamic_id', None) or getattr(it, 'id', None)
			except Exception:
				dyn_id = None
			logger.info(' -> new dynamic id: %s  raw: %s', dyn_id, it)

	if on_new is None:
		on_new = default_on_new

	while True:
		try:
			items = await get_dynamic_page_list(credential=credential, _type=_type, host_mid=host_mid, pn=1)
			new_items = []
			for it in items:
				# 尝试从不同位置获取动态 id（兼容不同版本返回结构）
				dyn_id = None
				try:
					desc = getattr(it, 'desc', None)
					if isinstance(desc, dict):
						dyn_id = desc.get('dynamic_id') or desc.get('rid') or desc.get('id')
				except Exception:
					dyn_id = None

				if dyn_id is None:
					# fallback
					dyn_id = getattr(it, 'dynamic_id', None) or getattr(it, 'id', None)

				if dyn_id is None:
					# 无法判断 id，全部当作新动态（保守策略）
					new_items.append(it)
				else:
					if dyn_id not in seen_ids:
						new_items.append(it)
						seen_ids.add(dyn_id)

			if new_items:
				# 如果指定了保存路径，把新动态追加保存为 JSON（按时间戳文件）
				if save_path:
					try:
						save_dynamics_to_json(new_items, save_path)
					except Exception:
						logger.exception('Failed to save dynamics to json')
				# 调用回调（允许是 coroutine 或普通函数）
				if asyncio.iscoroutinefunction(on_new):
					await on_new(new_items)
				else:
					on_new(new_items)

		except Exception as e:
			logger.exception('Error while polling dynamics: %s', e)

		await asyncio.sleep(poll_interval)


def _serialize_dynamic_item(item: Any) -> Any:
	"""Attempt to convert a Dynamic item to a JSON-serializable structure.

	The bilibili-api Dynamic objects may be dict-like or objects with attributes.
	We try common fallbacks and return a plain dict.
	"""
	# If it's already a dict-like
	if isinstance(item, dict):
		return item

	out = {}
	# try .__dict__ first
	if hasattr(item, '__dict__'):
		try:
			for k, v in vars(item).items():
				try:
					json.dumps(v)
					out[k] = v
				except Exception:
					# fallback to string
					out[k] = str(v)
			return out
		except TypeError:
			out = {}

	# try mapping-like access
	for k in ('desc', 'card', 'user', 'dynamic_id', 'id'):
		try:
			v = item[k]
		except Exception:
			try:
				v = getattr(item, k)
			except Exception:
				continue
		try:
			json.dumps(v)
			out[k] = v
		except Exception:
			out[k] = str(v)
	if out:
		return out

	# last resort
	return str(item)


def save_dynamics_to_json(items: List[Any], path: str) -> str:
	"""Save a list of dynamic items to a JSON file under the given directory.

	Returns the path of the written file.
	"""
	p = Path(path)
	p.mkdir(parents=True, exist_ok=True)
	import time
	ts = int(time.time())
	out_file = p.joinpath(f'dynamics_{ts}.json')
	serializable = [_serialize_dynamic_item(it) for it in items]
	with out_file.open('w', encoding='utf-8') as f:
		json.dump(serializable, f, ensure_ascii=False, indent=2)
	return str(out_file)


if __name__ == '__main__':
	# 简单 demo：取一次指定 up 的第一页动态并打印数量
	async def _demo():
		cred = make_credential()
		current_user_id = os.environ.get('BILIBILI_DEDEUSERID', '').strip()
		# 如果想监控当前登录用户，把环境变量 BILIBILI_DEDEUSERID 转成整数 mid。
		host_mid = int(current_user_id) if current_user_id and current_user_id.isdigit() else None
		items = await get_dynamic_page_list(credential=cred, host_mid=host_mid, pn=1)
		print(f'Fetched {len(items)} dynamics for host_mid={host_mid}')

		# 将 items 转为 JSON-可序列化结构并打印
		serializable = [_serialize_dynamic_item(it) for it in items]
		try:
			print('Dynamics JSON:')
			print(json.dumps(serializable, ensure_ascii=False, indent=2))
		except Exception:
			# 兜底打印原始对象
			for it in items:
				print(repr(it))

		# 同时保存到仓库下的一个 dynamics 文件夹并打印路径
		try:
			out = save_dynamics_to_json(items, path=str(Path(__file__).parent.joinpath('dynamics')))
			print(f'Saved dynamics file: {out}')
		except Exception:
			logger.exception('Failed to save dynamics in demo')

		# 如果有动态，获取第一个动态的详情并打印（使用 Dynamic 类）
		if items:
			# 提取第一个动态的 id
			dyn_id = None
			try:
				desc = getattr(items[0], 'desc', None)
				if isinstance(desc, dict):
					dyn_id = desc.get('dynamic_id') or desc.get('rid') or desc.get('id')
			except Exception:
				dyn_id = None
			if dyn_id is None:
				dyn_id = getattr(items[0], 'dynamic_id', None) or getattr(items[0], 'id', None)
			if dyn_id is not None:
				try:
					# 使用 Dynamic 类获取详情
					dyn_obj = Dynamic(int(dyn_id), cred)
					dyn_info = await dyn_obj.get_info()
					print('First-item detail via Dynamic.get_info():')
					print(json.dumps(_serialize_dynamic_item(dyn_info), ensure_ascii=False, indent=2))
				except Exception:
					logger.exception('Failed to fetch first item detail via Dynamic')
			else:
				# 未能提取 id，调用默认 id
				try:
					dyn_info = await get_dynamic_page_info(credential=cred)
					print('Dynamic detail JSON (default id):')
					print(json.dumps(dyn_info, ensure_ascii=False, indent=2))
				except Exception:
					logger.exception('Failed to fetch dynamic info for default id')

		# 演示 monitor：运行一次监控循环（非阻塞示例，实际可把下面注释改为长期运行）
		# await monitor_up(host_mid=host_mid, credential=cred, poll_interval=60)

	asyncio.run(_demo())