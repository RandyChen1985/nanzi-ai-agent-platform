-- V49: 增加用户密码最后修改时间字段与密码修改间隔天数配置
ALTER TABLE ai_agent_users ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMP WITHOUT TIME ZONE NULL;

INSERT INTO system_configs (key, value, description, category, is_secret) VALUES
(
  'password_expire_days',
  '30',
  '用户密码修改的有效间隔天数（天），个人中心将根据此间隔提醒用户及时修改密码，只能输入纯数字，默认 30 天。',
  'general',
  false
) ON CONFLICT (key) DO NOTHING;
