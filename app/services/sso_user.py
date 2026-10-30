import json
import logging
from urllib.parse import urlsplit, urlunsplit

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# requests 的 timeout 单位是「秒」而不是毫秒：原先写的 30000 等于 8.3 小时。
# 调用方若在事件循环里同步调用，SSO 不可达时会把整个进程（含所有 DB 请求）卡住。
# 这里保持同步实现（调用方已用 asyncio.to_thread 隔离），但把超时收敛到 30s。
_REQUEST_TIMEOUT_SECONDS = 30

# 用户列表的 path：域名不写死，跟着 SSO_API_URL 走，换环境只改一处配置。
_USER_LIST_PATH = "/api/v1/user/list"


def build_user_list_url(api_url: str | None = None) -> str:
    """由 SSO_API_URL 推出用户列表接口地址。

    登录是 `/user/check/login`、列表是 `/user/list`，同域不同 path，所以这里只复用
    「协议 + 域名 + 网关前缀」，不能直接拿 SSO_API_URL 当列表地址用。

    兼容三种配法：
    - https://yovole.net/api/v1/user/check/login → https://yovole.net/api/v1/user/list
    - https://yovole.net                         → https://yovole.net/api/v1/user/list
    - https://gw.example.com/laplace/api/v1/...  → https://gw.example.com/laplace/api/v1/user/list
    """
    raw = (settings.SSO_API_URL if api_url is None else api_url) or ""
    raw = raw.strip()
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        # 配错了要能一眼看出是配置问题，而不是在 requests 里抛一个看不懂的 URL 错误
        raise ValueError(f"SSO_API_URL 不是合法的绝对地址: {raw!r}")

    # SSO 挂在网关子路径下时（/laplace/api/...）这段前缀必须保留
    prefix = parts.path.split("/api/")[0].rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"{prefix}{_USER_LIST_PATH}", "", ""))


class LaplacePortalApiClient(object):
    @staticmethod
    def get_all_users():
        try:
            headers = {
                'YOVOLE-LAPLACE-API-ACCESS-TOKEN': settings.SSO_ACCESS_TOKEN,
                'Content-Type': 'application/json;charset=UTF-8'
            }
            api_url = build_user_list_url()

            # 先查询所有的用户
            api_request_all = {
                'requestSystem': settings.SSO_REQUEST_SYSTEM,
                'requestBusiness': 'USER-ADMIN-SYNC-AD-USER',
                'operationType': 'READ',
                'userStatus': -1,
                "userInfo": True
            }

            all_users = []

            # 查询 userStatus=0
            resp_0 = requests.post(url=api_url, headers=headers, data=json.dumps(api_request_all),
                                  timeout=_REQUEST_TIMEOUT_SECONDS, verify=False)
            if resp_0.status_code == 200:
                response_0 = json.loads(resp_0.content)
                for sso_user in response_0.get('data', []):
                    name = sso_user.get('displayName', '')
                    code = sso_user.get('loginName', '').lower()
                    email = sso_user.get('userEmail', '')
                    mobile = sso_user.get('userMobile', '')
                    department = sso_user.get('departmentName', '')
                    position = sso_user.get('positionName', '')
                    # 注意：这里行尾原本有个逗号，会让 status 变成元组 (0,)，下面的
                    # `status == 0` 恒为 False。本次只改 URL，所以保持原样未动，
                    # 待确认 userStatus 的语义后单独修。
                    status = sso_user.get('userStatus', 0),
                    userinfo = sso_user.get('userInfo', '')
                    if code:
                        all_users.append({
                            'code': code,
                            'name': name,
                            'email': email,
                            'status': status == 0,
                            'mobile': mobile,
                            'department': department,
                            'position': position,
                            'userinfo': userinfo
                        })

            return all_users
        except Exception as e:
            logger.error("auth_user error：%s" % e)
        return []
