"""YuNet 人脸检测（OpenCV Zoo，轻量 CNN）。

与 hog.py 保持相同对外接口：
    face_center(filename, model) -> (center_x, top) | None
未检到人脸时返回 None（注意 0 是合法坐标，不能用真假值判断）。

依赖：opencv-python-headless（缺失时本模块会抛 ImportError，
由 ImageProcessing.face_center 捕获并回退到下一个模型）。
模型：face_detection_yunet_2026may.onnx（约 229KB，OpenCV 5.x 官方默认动态维度版）。

模型搜索顺序（与 config.ini 一致，当前目录优先）：
  1. ./<模型>
  2. ./models/<模型>
  3. PyInstaller 解压目录 ImageProcessing/models/<模型>
  4. 包内 ImageProcessing/models/<模型>（仓库自带，兜底）
"""

import os
import sys

import cv2
import numpy as np

MODEL_NAME = 'face_detection_yunet_2026may.onnx'

# 检测器按输入尺寸缓存，避免每次重新加载模型
_detector = None
_detector_size = None


def _model_candidates():
    cwd = os.getcwd()
    here = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(cwd, MODEL_NAME),
        os.path.join(cwd, 'models', MODEL_NAME),
    ]
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        paths.append(os.path.join(meipass, 'ImageProcessing', 'models', MODEL_NAME))
    paths.append(os.path.join(here, 'models', MODEL_NAME))
    return paths


def _resolve_model_path():
    """返回第一个存在的模型路径；都不存在时返回包内路径，由调用方报错并回退。"""
    paths = _model_candidates()
    for path in paths:
        if os.path.isfile(path):
            return path
    return paths[-1]


def _create_detector(size):
    """兼容 OpenCV 4.x(FaceDetectorYN.create) 与 5.x(FaceDetectorYN_create)。"""
    create = getattr(cv2, 'FaceDetectorYN_create', None)
    if create is None:
        create = cv2.FaceDetectorYN.create
    path = _resolve_model_path()
    # (模型, 配置, 输入尺寸, 分数阈值, NMS 阈值, top_k)
    detector = create(path, '', size, 0.6, 0.3, 5000)
    print(f'[+]YuNet model: {path}')
    return detector


def _get_detector(width, height):
    global _detector, _detector_size
    size = (int(width), int(height))
    if _detector is None:
        _detector = _create_detector(size)
        _detector_size = size
    elif _detector_size != size:
        _detector.setInputSize(size)
        _detector_size = size
    return _detector


def _load_image(filename):
    """用 imdecode 读取，兼容 Windows 下的中文路径（cv2.imread 会失败）。"""
    with open(filename, 'rb') as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def face_center(filename, model):
    image = _load_image(filename)
    if image is None:
        return None

    height, width = image.shape[:2]
    detector = _get_detector(width, height)
    _, faces = detector.detect(image)

    if faces is None or len(faces) == 0:
        print('[+]Found person      [0]      By model yunet')
        return None

    # 与 hog 行为保持一致：取最右的人脸（x 中心最大）
    best = max(faces, key=lambda f: f[0] + f[2] / 2.0)
    x, y, w, h = best[0], best[1], best[2], best[3]
    print('[+]Found person      [' + str(len(faces)) + ']      By model yunet')
    return int(x + w / 2), int(y)
