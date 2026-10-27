import requests
import json
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# requests 的 timeout 单位是「秒」而不是毫秒：原先写的 30000 等于 8.3 小时。
# 调用方若在事件循环里同步调用，SSO 不可达时会把整个进程（含所有 DB 请求）卡住。
# 这里保持同步实现（调用方已用 asyncio.to_thread 隔离），但把超时收敛到 30s。
_REQUEST_TIMEOUT_SECONDS = 30

class LaplacePortalApiClient(object):
    @staticmethod
    def get_all_users():
        try:
            headers = {
                'YOVOLE-LAPLACE-API-ACCESS-TOKEN': settings.SSO_ACCESS_TOKEN,
                'Content-Type': 'application/json;charset=UTF-8'
            }
            api_url = 'https://yovole.net/api/v1/user/list'            
            
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