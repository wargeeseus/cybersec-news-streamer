import qrcode
from PIL import Image
from io import BytesIO


def generate_qr_code(url: str, size: int = 150) -> Image.Image:
    """
    Generate a QR code image for a URL.

    Args:
        url: The URL to encode
        size: The size of the QR code in pixels

    Returns:
        PIL Image of the QR code
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # Create with cyber theme colors
    qr_img = qr.make_image(fill_color="#00ff88", back_color="#0a0a0a")

    # Convert to PIL Image and resize
    if hasattr(qr_img, 'get_image'):
        img = qr_img.get_image()
    else:
        img = qr_img

    img = img.convert('RGBA')
    img = img.resize((size, size), Image.Resampling.LANCZOS)

    return img
