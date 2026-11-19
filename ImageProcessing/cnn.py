"""历史兼容模块。

config.ini 里的 locations_model=cnn 曾经规划为 dlib CNN 深度学习模型，
但从未实现，当前直接复用 hog 的实现（效果与 hog 完全相同）。
如需深度学习检测，请改用 yunet。
"""

import sys
sys.path.append('../')

from ImageProcessing.hog import face_center as hog_face_center


def face_center(filename, model):
    return hog_face_center(filename, model)
