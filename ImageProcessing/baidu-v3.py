from aip import AipFace
import config,base64


def face_center(filename, model):
    app_id = config.getInstance().conf.get("face", "appid")
    api_key = config.getInstance().conf.get("face", "key")
    app_secret = config.getInstance().conf.get("face", "secret")
    client = AipFace(app_id, api_key, app_secret)
    with open(filename, 'rb') as fp:
        base64_data = base64.b64encode(fp.read())
    result = client.detect(base64_data.decode('utf-8'),"BASE64")
    if result['error_msg']!='SUCCESS':
        raise ValueError(result['error_msg'])
    result = result['result']
    print('[+]Found face      ' + str(result['face_num']))
    # 
    found = False
    maxRight = 0
    maxTop = 0
    for face in result["face_list"]:
        location = face.get('location') or {}
        left = int(location.get('left', 0))
        top = int(location.get('top', 0))
        width = int(location.get('width', 0))
        if not found or left + width > maxRight:
            maxRight = left + width
            maxTop = top
            found = True
    # 未检到人脸时必须返回 None（0 是合法坐标），与 hog/yunet 保持一致
    if not found:
        return None
    return maxRight, maxTop
