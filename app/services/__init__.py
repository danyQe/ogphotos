from PIL import Image
from pillow_heif import register_heif_opener

# HEIC/HEIF support for every Image.open in the services package
register_heif_opener()

# Pillow's built-in decompression-bomb guard (~179 MP) rejects real panoramas.
# metadata.probe() enforces settings.max_image_pixels before anything is decoded.
Image.MAX_IMAGE_PIXELS = None
