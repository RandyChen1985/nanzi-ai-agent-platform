-- V48: 增加是否隐藏登录页 API Key Tab 的系统配置
INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret")
VALUES (
  'hide_login_apikey',
  'false',
  '是否隐藏登录页的 API Key (访问凭证) 登录选项卡。开启后登录页将不再展示 API Key 选项卡。',
  'general',
  0
)
ON CONFLICT ("key") DO NOTHING;
