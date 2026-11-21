from aip import AipBodyAnalysis
import config


def face_center(filename, model):
    app_id = config.getInstance().conf.get("face", "appid")
    api_key = config.getInstance().conf.get("face", "key")
    app_secret = config.getInstance().conf.get("face", "secret")
    client = AipBodyAnalysis(app_id, api_key, app_secret)
    with open(filename, 'rb') as fp:
        img = fp.read()
    result = client.bodyAnalysis(img)
    if 'error_code' in result:
        raise ValueError(result['error_msg'])
    print('[+]Found person      ' + str(result['person_num']))
    # 中心点取鼻子x坐标
    found = False
    maxRight = 0
    maxTop = 0
    for person_info in result["person_info"]:
        nose = (person_info.get('body_parts') or {}).get('nose') or {}
        if not nose:
            continue
        x = int(nose['x'])
        top = int(person_info['location']['top'])
        if not found or x > maxRight:
            maxRight = x
            maxTop = top
            found = True
    # 未检到人时必须返回 None（0 是合法坐标），与 hog/yunet 保持一致
    if not found:
        return None
    return maxRight, maxTop
