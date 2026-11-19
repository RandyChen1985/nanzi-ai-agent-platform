-- V221: 系统设置新增「图像」分组——文生图（generate_image 工具）所需的一组参数。（MySQL）
--
-- 与 PostgreSQL 侧 db-prod-pg/V121-add-image-generation-configs.sql 同一结构、同一语义。
--
-- 背景：平台此前只有「看图」能力（read_image 把图片喂给多模态模型），没有「出图」能力。
-- 本次新增 generate_image 内置工具，模型在对话里判断用户要图时自行调用，出图直接渲染在
-- 消息流中。工具要调外部图像模型服务，因此需要一组平台级配置：地址、密钥、模型 id、
-- 分辨率白名单、超时、单次张数、风格前缀。
--
-- 几个刻意的设计取舍：
--
--   1. 总开关默认关闭（'0'）。图像生成按张计费，默认开启会让「配好地址就能用」变成
--      「谁都不知道在花钱」。管理员显式打开之后，还要在每个智能体的工具清单里手动勾
--      generate_image —— 两道闸，缺一不可。
--
--   2. 关闭时工具**完全不暴露**给模型，而不是「暴露了但调用报错」。否则模型会先规划一步
--      生成、再收到一句「功能未开启」，白白消耗一轮上下文。
--
--   3. 分辨率用白名单（image_allowed_sizes）而不是让用户填任意值。两种接口格式对尺寸
--      的容忍度不同：聊天格式（openai_chat）遇到不认识的尺寸**不报错、静默回退**成默认
--      尺寸，用户拿到一张构图不对的图却查不到原因；原生图像格式（openai_images）会明确
--      报 `invalid size format`。白名单同时写进工具说明，模型只能从里面挑，两种协议下
--      都不会走到「悄悄回退」那条路。
--      格式统一为 `宽x高`（小写 x）：实测星号写成 `1328*1328` 会被聊天格式忽略并回退。
--
--   4. 密钥走 system_configs 的 is_secret=1（与 embed_api_key 一致），列表接口按
--      「前 3 位 + **** + 后 4 位」掩码返回；读取侧兼容带前缀的密文，便于后续把存量
--      明文密钥迁移成密文而不需要再改工具。
--
--   5. 不设配额。本期按「先看实际用量再决定要不要限制」处理，配额如后续需要，
--      沿用 knowledge_quota_* 的现成范式另加，不影响本文件的键名与语义。

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
    'image_generation_enabled',
    '0',
    '图像生成总开关。关闭时 generate_image 工具不会下发给任何智能体（模型看不到它，也不会产生空调用）；已生成的图片与产物链接保留不受影响。',
    'image',
    0
),
(
    'image_api_format',
    'openai_images',
    '图像接口格式。openai_images=原生 OpenAI 图像接口（/images/generations，尺寸与张数是正式参数，参数写错会明确报错）；openai_chat=聊天接口（/chat/completions，兼容性更广、模型 id 宽松匹配，但一次只能出一张且尺寸格式不对会被静默忽略）。',
    'image',
    0
),
(
    'image_api_base_url',
    '',
    '图像模型 API 地址，OpenAI 兼容。含版本号前缀、末尾不要带斜杠，例如 https://api.example.com/v1；平台按所选接口格式自己拼 /images/generations 或 /chat/completions。',
    'image',
    0
),
(
    'image_api_key',
    '',
    '图像模型 API 密钥。按 is_secret 掩码展示，读取侧兼容 modelkey:v1: 前缀的密文。',
    'image',
    1
),
(
    'image_model',
    '',
    '图像模型 id，必须与 GET {API 地址}/models 返回的 id 完全一致。手打容易差一个字母就 404，模型字段旁提供「从列表选择」按钮。',
    'image',
    0
),
(
    'image_default_size',
    '1024x1024',
    '默认分辨率，用户没在对话里指定比例时用它。格式必须是 宽x高（小写 x）。',
    'image',
    0
),
(
    'image_allowed_sizes',
    '1024x1024,1328x1328,1664x928,928x1664,1328x1008',
    '允许的分辨率白名单，逗号分隔。只有这里的值才会写进工具说明供模型挑选，避免模型挑出服务端不认的尺寸被静默回退。',
    'image',
    0
),
(
    'image_timeout_seconds',
    '120',
    '单次图像生成请求超时（秒）。覆盖整次 HTTP 往返：实测 1024x1024 约 4-5 秒，横版大图 6-7 秒，留足余量应对排队。',
    'image',
    0
),
(
    'image_max_images_per_call',
    '1',
    '单次工具调用最多生成几张（上限 4）。建议 1：每张单独计费，也最容易看清每张的耗时与失败原因。',
    'image',
    0
),
(
    'image_prompt_prefix',
    '',
    '风格前缀，非空时自动拼在用户提示词前面，用来统一企业视觉调性（例如「企业级扁平插画，主色 #0172a2 / #2563eb，留白充足」）。留空则完全按用户描述生成。',
    'image',
    0
);
