"""
图片处理工具模块

功能：
- 压缩图片到指定大小（适配 LLM API 限制）
- 格式转换（HEIC -> JPEG, PNG -> JPEG 等）
- 修正 EXIF 旋转
- 调整尺寸（保持比例）
- 综合处理并返回 base64 编码
"""

import io
import os
import base64
import logging
from typing import Optional, Tuple
from PIL import Image, ExifTags

from tools.registry import registry

logger = logging.getLogger(__name__)

# 支持的图片格式
SUPPORTED_FORMATS = {'JPEG', 'PNG', 'GIF', 'WEBP'}
# 格式到 MIME 类型的映射
FORMAT_TO_MIME = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'GIF': 'image/gif',
    'WEBP': 'image/webp'
}
# 文件扩展名到格式的映射
EXT_TO_FORMAT = {
    '.jpg': 'JPEG',
    '.jpeg': 'JPEG',
    '.png': 'PNG',
    '.gif': 'GIF',
    '.webp': 'WEBP',
    '.heic': 'HEIC',  # 需要转换
    '.heif': 'HEIC',  # 需要转换
}


class ImageProcessingError(Exception):
    """图片处理错误"""
    pass


def _get_exif_orientation(img: Image.Image) -> Optional[int]:
    """
    获取图片的 EXIF 旋转信息
    
    返回: EXIF Orientation 值 (1-8)，如果没有则返回 None
    """
    try:
        exif = img._getexif()
        if exif is None:
            return None
        
        for tag, value in exif.items():
            if ExifTags.TAGS.get(tag) == 'Orientation':
                return value
    except (AttributeError, KeyError, TypeError):
        pass
    return None


def fix_exif_rotation(img: Image.Image) -> Image.Image:
    """
    根据 EXIF 信息修正图片旋转
    
    EXIF Orientation 值对应的变换:
    1: 正常
    2: 水平翻转
    3: 旋转180度
    4: 垂直翻转
    5: 顺时针90度 + 水平翻转
    6: 顺时针90度
    7: 逆时针90度 + 水平翻转
    8: 逆时针90度
    """
    orientation = _get_exif_orientation(img)
    
    if orientation is None or orientation == 1:
        return img
    
    logger.debug(f"修正 EXIF 旋转: orientation={orientation}")
    
    operations = {
        2: (Image.Transpose.FLIP_LEFT_RIGHT,),
        3: (Image.Transpose.ROTATE_180,),
        4: (Image.Transpose.FLIP_TOP_BOTTOM,),
        5: (Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.ROTATE_90),
        6: (Image.Transpose.ROTATE_270,),
        7: (Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.ROTATE_270),
        8: (Image.Transpose.ROTATE_90,),
    }
    
    if orientation in operations:
        for op in operations[orientation]:
            img = img.transpose(op)
    
    return img


def _load_image(image_path: str) -> Image.Image:
    """
    加载图片文件
    
    参数:
    - image_path: 图片路径
    
    返回: PIL Image 对象
    
    异常: ImageProcessingError 如果文件不存在或无法加载
    """
    if not os.path.exists(image_path):
        raise ImageProcessingError(f"文件不存在: {image_path}")
    
    try:
        img = Image.open(image_path)
        # 加载图片数据到内存（避免文件被锁定）
        img.load()
        return img
    except Exception as e:
        raise ImageProcessingError(f"无法加载图片 {image_path}: {str(e)}")


def _get_format_from_path(image_path: str) -> str:
    """
    从文件路径获取图片格式
    """
    ext = os.path.splitext(image_path)[1].lower()
    return EXT_TO_FORMAT.get(ext, 'JPEG')


def resize_image(
    image_path: str,
    max_width: int,
    max_height: int,
    output_path: Optional[str] = None
) -> str:
    """
    调整图片尺寸（保持比例）
    
    参数:
    - image_path: 输入图片路径
    - max_width: 最大宽度
    - max_height: 最大高度
    - output_path: 输出路径（可选，默认覆盖原文件）
    
    返回: 输出文件路径
    """
    logger.info(f"调整图片尺寸: {image_path} -> max({max_width}x{max_height})")
    
    img = _load_image(image_path)
    
    # 修正 EXIF 旋转
    img = fix_exif_rotation(img)
    
    # 计算新尺寸（保持比例）
    original_width, original_height = img.size
    
    # 计算缩放比例
    width_ratio = max_width / original_width
    height_ratio = max_height / original_height
    ratio = min(width_ratio, height_ratio)
    
    # 如果不需要缩小，直接返回
    if ratio >= 1:
        logger.debug("图片尺寸已在限制范围内，无需调整")
        if output_path and output_path != image_path:
            img.save(output_path)
            return output_path
        return image_path
    
    # 计算新尺寸
    new_width = int(original_width * ratio)
    new_height = int(original_height * ratio)
    
    logger.debug(f"尺寸变化: {original_width}x{original_height} -> {new_width}x{new_height}")
    
    # 使用高质量重采样
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 保存
    output = output_path or image_path
    img_format = _get_format_from_path(output)
    if img_format in ('JPEG',) and img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    img.save(output, format=img_format, quality=95)
    logger.info(f"图片尺寸调整完成: {output}")
    
    return output


def convert_image(
    image_path: str,
    target_format: str = 'JPEG',
    output_path: Optional[str] = None
) -> str:
    """
    转换图片格式
    
    参数:
    - image_path: 输入图片路径
    - target_format: 目标格式 ('JPEG', 'PNG', 'GIF', 'WEBP')
    - output_path: 输出路径（可选）
    
    返回: 输出文件路径
    """
    target_format = target_format.upper()
    if target_format == 'JPG':
        target_format = 'JPEG'
    
    if target_format not in SUPPORTED_FORMATS:
        raise ImageProcessingError(f"不支持的目标格式: {target_format}")
    
    logger.info(f"转换图片格式: {image_path} -> {target_format}")
    
    img = _load_image(image_path)
    
    # 修正 EXIF 旋转
    img = fix_exif_rotation(img)
    
    # 处理透明通道
    if target_format == 'JPEG' and img.mode in ('RGBA', 'P', 'LA'):
        # JPEG 不支持透明，转换为 RGB
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        if img.mode in ('RGBA', 'LA'):
            background.paste(img, mask=img.split()[-1])
        img = background
        logger.debug("已将透明背景转换为白色")
    elif img.mode == 'P':
        img = img.convert('RGBA' if target_format == 'PNG' else 'RGB')
    
    # 确定输出路径
    if output_path is None:
        base_name = os.path.splitext(image_path)[0]
        ext = '.jpg' if target_format == 'JPEG' else f'.{target_format.lower()}'
        output_path = f"{base_name}{ext}"
    
    # 保存
    save_kwargs = {'format': target_format}
    if target_format == 'JPEG':
        save_kwargs['quality'] = 95
    elif target_format == 'WEBP':
        save_kwargs['quality'] = 95
    
    img.save(output_path, **save_kwargs)
    logger.info(f"格式转换完成: {output_path}")
    
    return output_path


def compress_image(
    image_path: str,
    max_size_kb: int = 1024,
    max_dimension: int = 2048,
    output_path: Optional[str] = None
) -> str:
    """
    压缩图片到指定大小
    
    参数:
    - image_path: 输入图片路径
    - max_size_kb: 最大文件大小 (KB)，默认 1024KB
    - max_dimension: 最大边长，默认 2048px
    - output_path: 输出路径（可选，默认覆盖原文件）
    
    返回: 输出文件路径
    
    压缩策略:
    1. 先调整尺寸到 max_dimension
    2. 逐步降低质量直到达到目标大小
    """
    logger.info(f"压缩图片: {image_path} -> max {max_size_kb}KB, max {max_dimension}px")
    
    img = _load_image(image_path)
    
    # 修正 EXIF 旋转
    img = fix_exif_rotation(img)
    
    # 1. 调整尺寸
    original_width, original_height = img.size
    if max(original_width, original_height) > max_dimension:
        ratio = max_dimension / max(original_width, original_height)
        new_width = int(original_width * ratio)
        new_height = int(original_height * ratio)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        logger.debug(f"尺寸调整: {original_width}x{original_height} -> {new_width}x{new_height}")
    
    # 确保是 RGB 模式（用于 JPEG 压缩）
    if img.mode in ('RGBA', 'P', 'LA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        if img.mode in ('RGBA', 'LA'):
            background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    # 2. 逐步降低质量直到达到目标大小
    max_size_bytes = max_size_kb * 1024
    quality = 95
    min_quality = 20
    
    while quality >= min_quality:
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        size = buffer.tell()
        
        logger.debug(f"质量={quality}, 大小={size/1024:.1f}KB")
        
        if size <= max_size_bytes:
            break
        
        quality -= 5
    
    # 保存结果
    output = output_path or image_path
    with open(output, 'wb') as f:
        f.write(buffer.getvalue())
    
    final_size = os.path.getsize(output) / 1024
    logger.info(f"压缩完成: {output}, 大小={final_size:.1f}KB, 质量={quality}")
    
    return output


def image_to_base64(
    image_path: str,
    target_format: str = 'JPEG'
) -> Tuple[str, str]:
    """
    将图片转换为 base64 编码
    
    参数:
    - image_path: 图片路径
    - target_format: 目标格式
    
    返回: (base64_string, mime_type)
    """
    img = _load_image(image_path)
    
    # 修正 EXIF 旋转
    img = fix_exif_rotation(img)
    
    target_format = target_format.upper()
    if target_format == 'JPG':
        target_format = 'JPEG'
    
    # 处理透明通道
    if target_format == 'JPEG' and img.mode in ('RGBA', 'P', 'LA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        if img.mode in ('RGBA', 'LA'):
            background.paste(img, mask=img.split()[-1])
        img = background
    
    # 编码为 base64
    buffer = io.BytesIO()
    save_kwargs = {'format': target_format}
    if target_format == 'JPEG':
        save_kwargs['quality'] = 95
    
    img.save(buffer, **save_kwargs)
    base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    mime_type = FORMAT_TO_MIME.get(target_format, 'image/jpeg')
    
    return base64_str, mime_type


def process_image_for_llm(
    image_path: str,
    max_size_kb: int = 1024,
    max_dimension: int = 2048
) -> dict:
    """
    综合处理图片用于 LLM API
    
    参数:
    - image_path: 图片路径
    - max_size_kb: 最大文件大小 (KB)
    - max_dimension: 最大边长
    
    返回: {
        "base64": "...",           # base64 编码
        "mime_type": "image/jpeg", # MIME 类型
        "data_url": "data:image/jpeg;base64,..." # 完整的 data URL
    }
    
    处理流程:
    1. 加载图片
    2. 修正 EXIF 旋转
    3. 调整尺寸（如超出限制）
    4. 压缩到目标大小
    5. 转换为 base64
    """
    logger.info(f"处理图片用于 LLM: {image_path}")
    
    img = _load_image(image_path)
    
    # 修正 EXIF 旋转
    img = fix_exif_rotation(img)
    
    # 调整尺寸
    original_width, original_height = img.size
    if max(original_width, original_height) > max_dimension:
        ratio = max_dimension / max(original_width, original_height)
        new_width = int(original_width * ratio)
        new_height = int(original_height * ratio)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        logger.debug(f"尺寸调整: {original_width}x{original_height} -> {new_width}x{new_height}")
    
    # 转换为 RGB（JPEG 格式）
    if img.mode in ('RGBA', 'P', 'LA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        if img.mode in ('RGBA', 'LA'):
            background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    # 压缩到目标大小
    max_size_bytes = max_size_kb * 1024
    quality = 95
    min_quality = 20
    
    while quality >= min_quality:
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        size = buffer.tell()
        
        if size <= max_size_bytes:
            break
        
        quality -= 5
    
    # 转换为 base64
    base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    mime_type = 'image/jpeg'
    data_url = f"data:{mime_type};base64,{base64_str}"
    
    logger.info(f"图片处理完成: 大小={size/1024:.1f}KB, 质量={quality}")
    
    return {
        "base64": base64_str,
        "mime_type": mime_type,
        "data_url": data_url
    }


# ===== 注册为 Tools =====

@registry.register(
    name="image_compress",
    description="压缩图片到指定大小，适合上传或发送",
    parameters={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径"
            },
            "max_size_kb": {
                "type": "integer",
                "description": "最大文件大小 (KB)，默认 1024"
            },
            "max_dimension": {
                "type": "integer",
                "description": "最大边长 (像素)，默认 2048"
            }
        },
        "required": ["image_path"]
    }
)
def image_compress(image_path: str, max_size_kb: int = 1024, max_dimension: int = 2048) -> str:
    """压缩图片 Tool"""
    try:
        output_path = compress_image(image_path, max_size_kb, max_dimension)
        final_size = os.path.getsize(output_path) / 1024
        return f"图片已压缩: {output_path}，大小: {final_size:.1f}KB"
    except ImageProcessingError as e:
        return f"压缩失败: {str(e)}"
    except Exception as e:
        logger.exception("图片压缩时发生错误")
        return f"压缩失败: {str(e)}"


@registry.register(
    name="image_convert",
    description="转换图片格式（支持 JPEG, PNG, GIF, WebP）",
    parameters={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径"
            },
            "target_format": {
                "type": "string",
                "description": "目标格式 (JPEG, PNG, GIF, WEBP)，默认 JPEG",
                "enum": ["JPEG", "PNG", "GIF", "WEBP"]
            }
        },
        "required": ["image_path"]
    }
)
def image_convert(image_path: str, target_format: str = 'JPEG') -> str:
    """转换图片格式 Tool"""
    try:
        output_path = convert_image(image_path, target_format)
        return f"格式转换完成: {output_path}"
    except ImageProcessingError as e:
        return f"转换失败: {str(e)}"
    except Exception as e:
        logger.exception("图片格式转换时发生错误")
        return f"转换失败: {str(e)}"


@registry.register(
    name="image_resize",
    description="调整图片尺寸（保持比例）",
    parameters={
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径"
            },
            "max_width": {
                "type": "integer",
                "description": "最大宽度 (像素)"
            },
            "max_height": {
                "type": "integer",
                "description": "最大高度 (像素)"
            }
        },
        "required": ["image_path", "max_width", "max_height"]
    }
)
def image_resize(image_path: str, max_width: int, max_height: int) -> str:
    """调整图片尺寸 Tool"""
    try:
        output_path = resize_image(image_path, max_width, max_height)
        img = Image.open(output_path)
        return f"尺寸调整完成: {output_path}，新尺寸: {img.size[0]}x{img.size[1]}"
    except ImageProcessingError as e:
        return f"调整失败: {str(e)}"
    except Exception as e:
        logger.exception("图片尺寸调整时发生错误")
        return f"调整失败: {str(e)}"
