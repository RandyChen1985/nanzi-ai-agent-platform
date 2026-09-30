-- V162: 增加账号自主注册申请开关（默认关闭）
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
  'user_registration_enabled',
  'false',
  '账号自主注册申请开关（默认关闭）。开启后登录页展示「申请账号」入口，申请人可自助提交注册申请；申请提交后账号为待审核状态，须由管理员在「用户管理 › 待审核」中审核通过后方可登录。',
  'general',
  0
);
