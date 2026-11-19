import sys
sys.path.append('../')

import logging
import os
import config
import importlib
from pathlib import Path
from PIL import Image
import shutil
from ADC_function import file_not_exist_or_empty


def _face_models():
    """读取 locations_model，返回去空白、去空项的模型名列表。"""
    try:
        models = config.getInstance().face_locations_model().lower().split(',')
    except Exception:
        models = ['hog']
    return [m.strip() for m in models if m.strip()]


def face_crop_width(filename, width, height):
    aspect_ratio = config.getInstance().face_aspect_ratio()
    # 裁剪宽度 = 高度/3 * aspect_ratio（与配置说明一致，所有分支统一）
    cropWidth = int(height / 3 * aspect_ratio)
    if cropWidth <= 0:
        cropWidth = 1
    if cropWidth > width:
        cropWidth = width

    for model in _face_models():
        face = face_center(filename, model)
        # 人脸贴边时 center/top 可能为 0，只能用 None 判断"未检到"
        if face is None:
            continue
        center = face[0]
        cropLeft = int(center - cropWidth / 2)
        cropRight = cropLeft + cropWidth
        # 越界处理
        if cropLeft < 0:
            cropLeft = 0
            cropRight = cropWidth
        elif cropRight > width:
            cropRight = width
            cropLeft = width - cropWidth
        return (max(cropLeft, 0), 0, min(cropRight, width), height)

    # 所有模型都没检到，默认靠右切
    return (width-cropWidth, 0, width, height)


def face_crop_height(filename, width, height):
    cropHeight = int(width*3/2)
    for model in _face_models():
        face = face_center(filename, model)
        if face is None:
            continue
        top = face[1]
        # 头部靠上
        cropTop = top
        cropBottom = cropHeight + top
        if cropBottom > height:
            cropTop = 0
            cropBottom = cropHeight
        if cropTop < 0:
            cropTop = 0
        return (0, cropTop, width, cropBottom)
    # 默认从顶部向下切割
    return (0, 0, width, cropHeight)


def cutImage(imagecut, path, thumb_path, poster_path, skip_facerec=False):
    conf = config.getInstance()
    fullpath_fanart = os.path.join(path, thumb_path)
    fullpath_poster = os.path.join(path, poster_path)
    aspect_ratio = conf.face_aspect_ratio()
    if conf.face_aways_imagecut():
        # 只对"本来不裁剪"的源强制裁剪，不覆盖各站自带的人像裁剪设定(imagecut=4)
        if imagecut == 0:
            imagecut = 1
    elif conf.download_only_missing_images() and not file_not_exist_or_empty(fullpath_poster):
        return
    # imagecut为4时同时也是有码影片 也用人脸识别裁剪封面
    if imagecut == 1 or imagecut == 4:  # 剪裁大封面
        try:
            img = Image.open(fullpath_fanart)
            width, height = img.size
            if width/height > 2/3:  # 如果宽度大于2
                if imagecut == 4:
                    # 以人像为中心切取
                    img2 = img.crop(face_crop_width(fullpath_fanart, width, height))
                elif skip_facerec:
                    # 有码封面默认靠右切
                    img2 = img.crop((width - int(height / 3 * aspect_ratio), 0, width, height))
                else:
                    # 以人像为中心切取
                    img2 = img.crop(face_crop_width(fullpath_fanart, width, height))
            elif width/height < 2/3:  # 如果高度大于3
                # 从底部向上切割
                img2 = img.crop(face_crop_height(fullpath_fanart, width, height))
            else:  # 如果等于2/3
                img2 = img
            is_jpeg = Path(fullpath_poster).suffix.lower() in ('.jpg', '.jpeg', '.jpge')
            if is_jpeg and img2.mode not in ('RGB', 'L'):
                # 目标为 JPEG 时先丢弃透明通道，避免 'cannot write mode RGBA as JPEG'
                img2 = img2.convert('RGB')
            if is_jpeg:
                # 不传 quality 时 Pillow 默认 75，二次压缩会让 poster 明显发糊
                img2.save(fullpath_poster, quality=95)
            else:
                img2.save(fullpath_poster)
            print(f"[+]Image Cutted!     {Path(fullpath_poster).name}")
        except Exception as e:
            print(e)
            print('[-]Cover cut failed!')
    elif imagecut == 0:  # 复制封面
        shutil.copyfile(fullpath_fanart, fullpath_poster)
        print(f"[+]Image Copyed!     {Path(fullpath_poster).name}")


def face_center(filename, model):
    """调用指定检测模型，返回 (center_x, top)；未检到或模型不可用返回 None。"""
    try:
        mod = importlib.import_module('.' + model, 'ImageProcessing')
        return mod.face_center(filename, model)
    except Exception as e:
        if config.getInstance().debug() == 1:
            logging.error(f'[-]Face model "{model}" failed on {filename}: {e}')
        return None

if __name__ == '__main__':
    cutImage(1,'z:/t/','p.jpg','o.jpg')
    #cutImage(1,'H:\\test\\','12.jpg','test.jpg')
