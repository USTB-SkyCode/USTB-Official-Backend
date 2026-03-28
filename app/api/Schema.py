"""Request validation schemas for API payloads and query parameters."""

import html

from marshmallow import Schema, ValidationError, fields, pre_load, validates_schema, validate


def _normalize_string(value):
	if value is None:
		return None
	if isinstance(value, str):
		value = value.strip()
		return value or None
	return value


def _validate_server_address(value):
	if value is None:
		return
	if any(ch.isspace() for ch in value):
		raise ValidationError('服务器地址不能包含空白字符')
	if len(value) > 255:
		raise ValidationError('服务器地址长度不能超过 255')


class McServerCreateSchema(Schema):
	ip = fields.Str(required=False, allow_none=True, validate=_validate_server_address)
	name = fields.Str(required=False, allow_none=True, validate=validate.Length(max=100))
	expose_ip = fields.Bool(required=False, allow_none=True)

	@pre_load
	def normalize_payload(self, data, **kwargs):
		payload = dict(data or {})
		payload['ip'] = _normalize_string(payload.get('ip'))
		payload['name'] = _normalize_string(payload.get('name'))
		return payload

	@validates_schema
	def validate_payload(self, data, **kwargs):
		ip = data.get('ip')
		if not ip:
			raise ValidationError('创建服务器时 ip 为必填项', 'ip')


class McServerUpdateSchema(Schema):
	ip = fields.Str(required=False, allow_none=True, validate=_validate_server_address)
	name = fields.Str(required=False, allow_none=True, validate=validate.Length(max=100))
	expose_ip = fields.Bool(required=False, allow_none=True)

	@pre_load
	def normalize_payload(self, data, **kwargs):
		payload = dict(data or {})
		payload['ip'] = _normalize_string(payload.get('ip'))
		payload['name'] = _normalize_string(payload.get('name'))
		return payload

	@validates_schema
	def validate_payload(self, data, **kwargs):
		if not data:
			raise ValidationError('更新服务器时至少提供一个字段')
		if data.get('ip') is None and data.get('name') is None and data.get('expose_ip') is None:
			raise ValidationError('更新服务器时至少提供 ip、name、expose_ip 之一')


class McServerSortSchema(Schema):
	id_list = fields.List(
		fields.Int(validate=validate.Range(min=0)),
		required=True,
		validate=validate.Length(min=1, max=200),
	)

	@validates_schema
	def validate_payload(self, data, **kwargs):
		id_list = data.get('id_list') or []
		if len(set(id_list)) != len(id_list):
			raise ValidationError('id_list 不能包含重复值', 'id_list')


class McServerStatusQuerySchema(Schema):
	include_icon = fields.Bool(load_default=True)


class RssFeedListQuerySchema(Schema):
	limit = fields.Int(load_default=20, validate=validate.Range(min=1, max=100))
	offset = fields.Int(load_default=0, validate=validate.Range(min=0))


class RssFeedEntryQuerySchema(Schema):
	feed_id = fields.Int(required=False, allow_none=True, validate=validate.Range(min=1))
	limit = fields.Int(load_default=20, validate=validate.Range(min=1, max=100))
	offset = fields.Int(load_default=0, validate=validate.Range(min=0))


def _validate_storage_key(value):
	if value is None:
		return
	if any(ch.isspace() for ch in value):
		raise ValidationError('storage_key 不能包含空白字符')
	if value.startswith('/') or value.startswith('\\'):
		raise ValidationError('storage_key 不能以路径分隔符开头')
	if '..' in value.split('/'):
		raise ValidationError('storage_key 不能包含 .. 路径段')
	if '\\' in value:
		raise ValidationError('storage_key 不能包含反斜杠')
	if value.endswith('/'):
		raise ValidationError('storage_key 必须指向文件，不能以 / 结尾')
	if len(value) > 1024:
		raise ValidationError('storage_key 长度不能超过 1024')


class FileListQuerySchema(Schema):
	limit = fields.Int(load_default=20, validate=validate.Range(min=1, max=100))
	offset = fields.Int(load_default=0, validate=validate.Range(min=0))
	include_inactive = fields.Bool(load_default=False)


class FileCreateSchema(Schema):
	storage_key = fields.Str(required=True, validate=_validate_storage_key)
	display_name = fields.Str(required=True, validate=validate.Length(min=1, max=255))
	download_name = fields.Str(required=False, allow_none=True, validate=validate.Length(max=255))
	description = fields.Str(required=False, allow_none=True, validate=validate.Length(max=2000))
	mime_type = fields.Str(required=False, allow_none=True, validate=validate.Length(max=255))
	size_bytes = fields.Int(required=False, allow_none=True, validate=validate.Range(min=0))
	visibility = fields.Str(
		required=False,
		load_default='authenticated',
		validate=validate.OneOf(['public', 'authenticated', 'admin'])
	)
	is_active = fields.Bool(required=False, load_default=True)
	metadata = fields.Dict(required=False, allow_none=True)

	@pre_load
	def normalize_payload(self, data, **kwargs):
		payload = dict(data or {})
		for key in ('storage_key', 'display_name', 'download_name', 'description', 'mime_type', 'visibility'):
			if key in payload:
				payload[key] = _normalize_string(payload.get(key))
		return payload

	@validates_schema
	def validate_payload(self, data, **kwargs):
		if not data.get('storage_key'):
			raise ValidationError('创建文件时 storage_key 为必填项', 'storage_key')
		if not data.get('display_name'):
			raise ValidationError('创建文件时 display_name 为必填项', 'display_name')


class FileUpdateSchema(Schema):
	storage_key = fields.Str(required=False, allow_none=True, validate=_validate_storage_key)
	display_name = fields.Str(required=False, allow_none=True, validate=validate.Length(min=1, max=255))
	download_name = fields.Str(required=False, allow_none=True, validate=validate.Length(max=255))
	description = fields.Str(required=False, allow_none=True, validate=validate.Length(max=2000))
	mime_type = fields.Str(required=False, allow_none=True, validate=validate.Length(max=255))
	size_bytes = fields.Int(required=False, allow_none=True, validate=validate.Range(min=0))
	visibility = fields.Str(required=False, allow_none=True, validate=validate.OneOf(['public', 'authenticated', 'admin']))
	is_active = fields.Bool(required=False, allow_none=True)
	metadata = fields.Dict(required=False, allow_none=True)

	@pre_load
	def normalize_payload(self, data, **kwargs):
		payload = dict(data or {})
		for key in ('storage_key', 'display_name', 'download_name', 'description', 'mime_type', 'visibility'):
			if key in payload:
				payload[key] = _normalize_string(payload.get(key))
		return payload

	@validates_schema
	def validate_payload(self, data, **kwargs):
		if not data:
			raise ValidationError('更新文件时至少提供一个字段')
		if all(data.get(key) is None for key in ('storage_key', 'display_name', 'download_name', 'description', 'mime_type', 'size_bytes', 'visibility', 'is_active', 'metadata')):
			raise ValidationError('更新文件时至少提供一个字段')


class FileDownloadVerifyQuerySchema(Schema):
	token = fields.Str(required=True, validate=validate.Length(min=1, max=4096))


class FileDownloadAuditQuerySchema(Schema):
	file_id = fields.Int(required=False, allow_none=True, validate=validate.Range(min=1))
	action = fields.Str(required=False, allow_none=True, validate=validate.OneOf(['issue_token', 'authorize']))
	outcome = fields.Str(required=False, allow_none=True, validate=validate.OneOf(['success', 'denied']))
	limit = fields.Int(load_default=20, validate=validate.Range(min=1, max=100))
	offset = fields.Int(load_default=0, validate=validate.Range(min=0))

class UserSchema(Schema):
	user_id = fields.Str()
	username = fields.Method("get_username")
	email = fields.Email()
	avatar_url = fields.Str()
	login_time = fields.Str()
	provider = fields.Str()
	permission = fields.Int()

	def get_username(self, obj):
		"""Escape usernames before serializing them back into HTML-capable clients."""
		return html.escape(obj.get('username', ''))