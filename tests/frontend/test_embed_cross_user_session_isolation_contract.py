"""EmbedChat 跨用户会话隔离的源码级契约。

## 背景

同一浏览器换用户登录（门户退出换账号、或同一 tab 由宿主重新签发票据）后，EmbedChat
曾经会加载到**上一位用户**的会话，原因是三处状态都没有身份边界：

1. 会话 ID 只按 `instance_id` 存在 localStorage（`yovole_embed_conv_id[:<instance>]`），
   后一位用户读到前一位用户的 ID，随后 `/chat/history` 就按这个 ID 拉历史；
2. 内存里的 `messages` / 分页游标 / 资源范围不会因为身份变化而清空；即便服务端返回
   空历史（他人会话对该用户就是空），旧消息仍留在界面上；
3. 流式草稿快照（sessionStorage）同样只按 `实例 + 会话` 分桶，会把对方会话的正文
   还原到新用户界面上。

## 约定

- 会话与草稿的存储键必须带**用户维度**，且**绝不读取**没有用户维度的旧键（只清理）；
- 身份变化时丢弃上一位用户的整个会话现场（消息、会话 ID、分页、资源范围、草稿）；
- 任何来源的会话 ID（宿主下发 / 本地残留 / 服务端活跃会话）在采用前必须过归属校验，
  校验失败一律不采用；
- 首屏历史为空时必须清空消息（不能保留上一位用户已渲染的内容）。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _init_chat_body() -> str:
    source = _source()
    return source[source.index("const initChat = async"):source.index("// History State")]


def _validate_token_body() -> str:
    source = _source()
    return source[source.index("const validateToken = async"):source.index("const initChat = async")]


def test_conversation_storage_key_carries_the_user_identity():
    source = _source()

    assert "const currentIdentityKey = (): string =>" in source
    assert "const identityKeyFromUserData = (data: any): string =>" in source
    assert 'const USER_SCOPED_STORAGE_SEGMENT = "u";' in source
    assert "const scope = `${USER_SCOPED_STORAGE_SEGMENT}:${encodeURIComponent(identity)}`;" in source
    # 身份未知时不读不写：否则会退回无维度的键，等于把隔离换个地方绕开。
    assert 'if (!identity) return "";' in source
    # 键形如 yovole_embed_conv_id:u:<identity>[:<instance>]
    assert "return instanceId" in source
    assert "`${INSTANCE_CONVERSATION_STORAGE_PREFIX}${scope}:${encodeURIComponent(instanceId)}`" in source
    assert "`${INSTANCE_CONVERSATION_STORAGE_PREFIX}${scope}`" in source


def test_legacy_unscoped_conversation_keys_are_purged_but_never_read():
    source = _source()
    reader = source[source.index("const readStoredConversationId = (): string =>"):source.index("const clearStoredConversationId")]

    assert "const purgeLegacyConversationStorage = () =>" in source
    assert "purgeLegacyConversationStorage();" in source
    assert "localStorage.getItem(LEGACY_CONVERSATION_STORAGE_KEY)" not in source
    assert "const key = conversationStorageKey();" in reader
    assert "localStorage.getItem(key)" in reader
    assert "LEGACY_CONVERSATION_STORAGE_KEY" not in reader, "旧的无维度键只清理、不得读取"


def test_stream_snapshot_storage_key_carries_the_user_identity():
    source = _source()

    assert "const purgeLegacyStreamSnapshots = () =>" in source
    assert "purgeLegacyStreamSnapshots();" in source
    assert "${USER_SCOPED_STORAGE_SEGMENT}:${encodeURIComponent(identity)}" in source


def test_identity_change_resets_the_previous_users_session_view():
    source = _source()
    init_chat = _init_chat_body()

    assert "const resetSessionForIdentityChange = (" in source
    reset_body = source[
        source.index("const resetSessionForIdentityChange = ("):source.index("const generateNewConversation = (")
    ]
    # 会话现场的每一项都必须清掉：只换存储键会留下看得见的上一位用户对话。
    for statement in (
        "messages.value = [];",
        'conversationId.value = "";',
        "resourceScope.value = emptyResourceScopeState();",
        "historyOffset.value = 0;",
        "hasMoreHistory.value = true;",
        "activeStreamDraft = null;",
        "clearStreamReplayTimer();",
        "resetEmbedThinkingOverrides();",
        "activeIdentityKey = nextIdentityKey;",
    ):
        assert statement in reset_body, f"身份切换重置缺少：{statement}"

    # 界面现场：会话数据清了不等于界面清了（这些曾经全部漏清，会直接把上一位用户的
    # 内容显示给新用户，甚至让新用户继续操作对方的浏览器会话）。
    for statement in (
        "historyList.value = [];",
        "historyKeyword.value = \"\";",
        "browserPanelVisible.value = false;",
        "browserSessionId.value = null;",
        "browserViewerToken.value = null;",
        "webPreviewVisible.value = false;",
        "webPreviewUrl.value = null;",
        'userInput.value = "";',
        "clearActiveMetadataDatasets(chatInputRef.value);",
        "traceLogData.value = null;",
        "activeHistoryItem.value = null;",
        "conversationTurns.value = [];",
        "currentStats.value = [];",
        "hasFetchedAgents.value = false;",
        "injectedContext.value = {};",
        "config.welcomeMessage = welcomeMessageFromHost;",
        "stopRemoteRunPolling();",
        "streamReplayPrimed = false;",
    ):
        assert statement in reset_body, f"身份切换重置缺少：{statement}"
    # 浏览器面板的在途打开请求也要作废，否则旧请求回来会把面板重新挂上对方的会话。
    assert "browserOpenGeneration += 1;" in reset_body

    # 身份变化必须真的触发重置（挂在鉴权成功后、采用的会话 ID 之前）。
    assert "if (identityKey && identityKey !== activeIdentityKey) {" in init_chat
    assert "resetSessionForIdentityChange(identityKey, { preserveRequestedConversationId: true });" in init_chat
    assert init_chat.index("identityKey !== activeIdentityKey") < init_chat.index("readStoredConversationId()")


def test_every_adopted_conversation_id_is_ownership_checked_first():
    source = _source()
    init_chat = _init_chat_body()

    assert "const checkConversationAdoption = async (" in source
    assert "/ownership`" in source
    # 宿主下发、本地残留、服务端活跃会话三处都要过校验。
    assert init_chat.count("await checkConversationAdoption(") >= 3
    assert "const requestedAdoption = await checkConversationAdoption(requested);" in init_chat
    assert "const savedAdoption = savedId ? await checkConversationAdoption(savedId) : 'unknown';" in init_chat
    assert "const activeAdoption = await checkConversationAdoption(activeCid);" in init_chat
    # 校验只有三种结论，且只有 adoptable 才允许采用。
    assert "Promise<'adoptable' | 'foreign' | 'unknown'>" in source
    assert "if (requestedAdoption === 'adoptable')" in init_chat
    assert "if (savedId && savedAdoption === 'adoptable')" in init_chat
    assert "if (activeAdoption === 'adoptable')" in init_chat
    # 不采用时必须丢弃宿主下发的待采用 ID；服务端活跃指针交由新建会话覆盖。
    assert "requestedConversationId = \"\";" in init_chat
    # 只有确认「属于别人」才清本地记录：网络异常（unknown）清掉等于平白删掉用户自己的续接记录。
    assert "if (requestedAdoption === 'foreign') clearStoredConversationId();" in init_chat
    assert "else if (savedId && savedAdoption === 'foreign')" in init_chat


def test_adoption_results_are_generation_guarded_before_being_used():
    """归属校验是异步的：await 回来后代次可能已经变了，必须在使用结论前再确认一次。

    否则上一代初始化的结论会写进新一代的状态里（新一代刚重置完，又被写入一个属于
    上一位用户的会话 ID）。
    """
    init_chat = _init_chat_body()
    lines = init_chat.splitlines()

    checked = 0
    for index, line in enumerate(lines):
        if "await checkConversationAdoption(" not in line:
            continue
        checked += 1
        window = "\n".join(lines[index + 1:index + 4])
        assert "initGeneration !== conversationInitializationGeneration" in window, (
            f"await 归属校验之后缺少代次复核：{line.strip()}"
        )
    assert checked >= 3, "三处采用点都应各自做代次复核"


def test_history_sidebar_click_validates_ownership_before_switching():
    """侧栏列表也可能来自上一位用户的旧列表，点选同样要先过归属校验。"""
    source = _source()
    handler = source[
        source.index("const handleHistoryClick = async (item: any) => {"):
        source.index("const handleDeleteHistory = async (traceId: string) => {")
    ]

    assert "const adoption = await checkConversationAdoption(target);" in handler
    assert "if (adoptionSequence !== historyAdoptionSequence) return;" in handler
    assert "if (adoption !== 'adoptable') {" in handler
    # 采用动作必须发生在校验通过之后。
    assert handler.index("checkConversationAdoption(target)") < handler.index("conversationId.value = target;")


def test_host_supplied_conversation_id_is_not_persisted_before_the_check():
    """宿主下发的会话 ID 只是「待采用」：未过归属校验前不得写入内存或 localStorage。"""
    source = _source()
    apply_body = source[
        source.index("const applyInitConfigPayload = "):source.index("const applyInitConfigPayload = ") + 6000
    ]
    block = apply_body[apply_body.index("if (data.conversation_id) {"):]
    block = block[:block.index("} else if (!data.agent_id) {")]

    assert "requestedConversationId = String(data.conversation_id);" in block
    assert "conversationId.value = requestedConversationId;" not in block
    assert "persistConversationId(requestedConversationId);" not in block


def test_validate_token_never_trades_a_rejected_explicit_credential_for_a_stored_one():
    """宿主显式下发的凭据被拒时，绝不能拿本 tab 存储的令牌顶上。

    存储的令牌属于上一位使用者：顶上去等于宿主想切到 B、页面却静默以 A 的身份继续跑。
    """
    body = _validate_token_body()

    assert "if (key === explicitToken) {" in body
    assert "not falling back to the stored session" in body
    # 该分支必须直接失败返回，而不是继续尝试下一个候选。
    explicit_guard = body[body.index("if (key === explicitToken) {"):]
    explicit_guard = explicit_guard[:explicit_guard.index("}")]
    assert "return false;" in explicit_guard
    assert "continue;" not in explicit_guard


def test_first_page_history_always_overwrites_messages_even_when_empty():
    source = _source()
    fetch_body = source[
        source.index("const fetchConversationHistory = async"):source.index("const showQuotaStatusInChat")
    ]

    assert "if (newHistoryBatch.length === 0 && !isLoadMore) {" in fetch_body
    assert "messages.value = [];" in fetch_body
    # 空结果清空必须发生在「非空才覆盖」的分支之外，二者不能互相嵌套。
    empty_guard_at = fetch_body.index("if (newHistoryBatch.length === 0 && !isLoadMore) {")
    non_empty_guard_at = fetch_body.index("if (newHistoryBatch.length > 0) {")
    assert empty_guard_at < non_empty_guard_at


def test_validate_token_reconciles_stored_credential_with_cookie_identity():
    body = _validate_token_body()

    assert "const switchToCookieIdentityIfChanged = async (): Promise<boolean> =>" in body
    assert "clearEmbedSession();" in body
    assert "const attachCookieIdentity = (data: Record<string, unknown>) =>" in body
    # 显式凭据仍然 fail-closed；仅存储令牌允许让位给 Cookie 身份。
    assert "const explicitToken = String(config.token || \"\").trim();" in body
    assert "key === storedSessionCredential && key !== explicitToken" in body


def test_no_component_reads_the_legacy_unscoped_conversation_key():
    """「只清理、不读取」要覆盖所有组件，而不只是 EmbedChat 自己。

    ChatCanvas / MessageRenderer 曾经在会话 ID 缺失时回退读 `yovole_embed_conv_id`：
    该键没有用户维度（老版本写入、清过之后还可能被仍在跑的旧页面写回来），读到的
    可能是上一位使用者的会话 ID。
    """
    offenders = []
    for path in sorted((ROOT / "frontend/src").rglob("*.vue")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if "yovole_embed_conv_id" in stripped and "getItem" in stripped:
                offenders.append(f"{path.relative_to(ROOT)}: {stripped}")

    assert offenders == [], f"仍有组件读取无维度的旧会话键：{offenders}"
    # 只清理的地方（EmbedChat 的 purge 函数）仍然存在。
    embed = _source()
    assert "const purgeLegacyConversationStorage = () =>" in embed
    assert "localStorage.removeItem(LEGACY_CONVERSATION_STORAGE_KEY)" in embed


def test_stream_snapshot_storage_is_fail_closed_without_an_identity():
    """草稿快照含会话正文：身份未知时不产生存储键，且读写清三处都跳过。"""
    source = _source()
    key_fn = source[
        source.index("const streamSnapshotStorageKey = (cid: string): string =>"):
        source.index("/** 清掉没有用户维度的历史草稿键")
    ]

    assert "const identity = currentIdentityKey();" in key_fn
    assert 'if (!identity || !cid) return "";' in key_fn
    assert '"unknown"' not in key_fn, "不得回退到共享/匿名桶"

    for fn_name, next_marker in (
        ("const clearStreamSnapshot = (cid: string) =>", "const STREAM_REPLAY_POLL_INTERVAL_MS"),
        ("const readStreamSnapshot = (cid: string): EmbedStreamSnapshot | null =>", "let lastStreamSnapshotAt"),
    ):
        body = source[source.index(fn_name):source.index(next_marker, source.index(fn_name))]
        assert "if (!key) return" in body, f"{fn_name} 未对空键做短路"

    persist = source[
        source.index("const persistStreamSnapshot = ("):
        source.index("const readStreamSnapshot = (cid: string)")
    ]
    assert "const key = streamSnapshotStorageKey(cid);" in persist
    assert "if (!key) return;" in persist
    assert "sessionStorage.setItem(key, JSON.stringify(snapshot));" in persist


def test_host_welcome_message_override_survives_identity_change():
    """宿主下发过欢迎语覆盖文案时，换身份要恢复宿主口径，而不是上一位用户的姓名。"""
    source = _source()

    assert "let welcomeMessageFromHost = \"\";" in source
    apply_body = source[
        source.index("const applyInitConfigPayload = "):source.index("const applyInitConfigPayload = ") + 6000
    ]
    assert "welcomeMessageFromHost = String(data.welcome_message_override);" in apply_body
    assert "config.welcomeMessage = welcomeMessageFromHost;" in apply_body


def _check_adoption_body() -> str:
    source = _source()
    return source[
        source.index("const checkConversationAdoption = async ("):
        source.index("const resetSessionForIdentityChange = (")
    ]


def _history_click_body() -> str:
    source = _source()
    return source[
        source.index("const handleHistoryClick = async (item: any) => {"):
        source.index("const handleDeleteHistory = async (traceId: string) => {")
    ]


def test_mixed_conversation_with_my_own_turns_is_still_adoptable():
    """一个 cid 里既有别人的轮次、也有我自己的轮次时，必须放行而不是拦下。

    这种「一个会话 ID、多个归属人」的历史数据很常见：打补丁前会话 ID 与登录身份无关，
    同一浏览器换人登录会沿用同一个 cid；宿主下发的 resume id 也可能被同租户多人共用。
    读侧本来就按 user_id 过滤（历史只回我自己的轮次、Redis 记忆键也带 user_id），
    拦下这类会话不会多挡住任何跨用户内容，只会让用户打不开自己参与过的历史；前端一旦
    判成 foreign 还会清掉本地续接指针，表现就是「刷新后内容没了、像开了新会话」。
    """
    check = _check_adoption_body()

    assert "const owned = data.owned === true;" in check
    assert "const foreign = data.foreign === true;" in check
    # 拦住的条件必须是「有别人的轮次 **且** 没有我自己的轮次」。
    assert "if (foreign && !owned) {" in check
    blocked_at = check.index("if (foreign && !owned) {")
    adopt_at = check.index("return 'adoptable';")
    assert blocked_at < adopt_at, "混杂会话必须走到 adoptable，而不是被当成别人的会话"
    # 只报「别人的轮次」的旧判定会让混杂会话被误拦。
    assert "if (res.data?.data?.foreign === true) {" not in check


def test_history_click_toast_separates_foreign_ownership_from_a_failed_check():
    """「确实是别人的会话」和「归属暂时查不出来」不能共用一句提示。"""
    handler = _history_click_body()

    assert "adoption === 'foreign'" in handler
    assert "该会话属于其他用户，已阻止在当前身份下打开" in handler
    assert "会话归属暂时无法确认，请稍后重试" in handler
    # 旧文案把两种结论混为一谈（查不出来时也说「包含其他用户的记录」）。
    assert "该会话包含其他用户的记录" not in handler


def test_unverifiable_ownership_does_not_overwrite_the_stored_pointer():
    """归属校验失败（unknown）时兜底新建的会话不得落盘，否则等于抹掉用户的续接记录。

    校验失败只说明「此刻问不出来」，不是「这会话不是你的」。若此时用新会话覆盖本地指针
    与服务端活跃指针，用户下次打开就只剩一个空会话，看起来像「刷新后内容没了」。
    """
    init_chat = _init_chat_body()
    source = _source()

    assert "let adoptionUnknown = false;" in init_chat
    assert "if (requestedAdoption === 'unknown') adoptionUnknown = true;" in init_chat
    assert "} else if (savedId && savedAdoption === 'unknown') {" in init_chat
    assert "if (activeAdoption === 'unknown') adoptionUnknown = true;" in init_chat
    assert "generateNewConversation({ persist: !adoptionUnknown });" in init_chat
    # 只有确认属于别人（foreign）才允许清掉本地指针。
    assert "if (requestedAdoption === 'foreign') clearStoredConversationId();" in init_chat

    generate_at = source.index(
        "const generateNewConversation = (options: { persist?: boolean } = {}) => {"
    )
    generate = source[generate_at:source.index("\n};", generate_at)]
    guard_at = generate.index("if (options.persist !== false) {")
    assert guard_at < generate.index("persistConversationId(conversationId.value);"), (
        "persist: false 时必须跳过本地指针写入"
    )
    assert guard_at < generate.index("updateActiveConversationOnServer(conversationId.value);"), (
        "persist: false 时也必须跳过服务端活跃指针写入"
    )


def test_adoption_check_retries_before_giving_up():
    """接口抖动不该直接降级成「不采用」：先重试一次再下结论。"""
    check = _check_adoption_body()

    assert "for (let attempt = 0; attempt < 2; attempt += 1) {" in check
