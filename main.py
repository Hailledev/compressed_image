from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from PIL import Image
import io
from starlette.responses import StreamingResponse
from pymongo import MongoClient
from datetime import datetime
import os
import uuid

app = FastAPI(title="NÉN ẢNH")

# Kết nối MongoDB
client = MongoClient("mongodb://localhost:27017")
db = client["image_db"]
collection = db["images"]

# Thư mục tạm để lưu ảnh nén 
TEMP_DIR = "temp_images"
os.makedirs(TEMP_DIR, exist_ok=True)

# Hàm nén và resize ảnh với tham số tùy chỉnh
async def compress_image(input_image: UploadFile, max_dimension: int = 500, target_size_kb: int = 100, min_quality: int = 75, max_quality: int = 90):
    """
    Nén ảnh với một cạnh cố định 500px, giữ tỷ lệ gốc, dung lượng 100-150KB.
    Parameters:
        input_image: File ảnh đầu vào
        max_dimension: Kích thước tối đa của cạnh dài nhất (mặc định 500px)
        target_size_kb: Dung lượng mục tiêu (KB, mặc định 100-150KB)
        min_quality: Chất lượng JPEG tối thiểu (75 để giữ ảnh ổn)
        max_quality: Chất lượng JPEG tối đa (90 để bắt đầu)
    Returns:
        output: BytesIO chứa ảnh nén
        new_dimensions: Kích thước ảnh sau khi resize
        original_size: Dung lượng trước khi nén (bytes)
        compressed_size: Dung lượng sau khi nén (bytes)
    """
    # Đọc nội dung file ảnh
    content = await input_image.read()
    original_size = len(content)
    img = Image.open(io.BytesIO(content))
    
    # Chuyển sang RGB nếu ảnh là PNG hoặc định dạng khác
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Tính toán dimensions dựa trên tỷ lệ gốc, giữ một cạnh là 500px
    width, height = img.size
    if width >= height:  # Ảnh chữ nhật ngang hoặc vuông
        new_width = max_dimension
        new_height = int((max_dimension / width) * height)
    else:  # Ảnh chữ nhật dọc
        new_height = max_dimension
        new_width = int((max_dimension / height) * width)
    
    max_dimensions = (new_width, new_height)
    
    # Resize ảnh giữ tỷ lệ
    img.thumbnail(max_dimensions, Image.Resampling.LANCZOS)
    
    # Lưu ảnh vào bộ nhớ đệm để kiểm tra dung lượng
    output = io.BytesIO()
    quality = max_quality
    img.save(output, format='JPEG', quality=quality, optimize=True)
    
    # Giảm chất lượng dần nếu dung lượng vượt quá 150KB
    while output.tell() > 150 * 1024 and quality > min_quality:
        quality -= 5
        output.seek(0)
        output.truncate(0)
        img.save(output, format='JPEG', quality=quality, optimize=True)
    
    # Nếu vẫn vượt quá 150KB, giảm thêm dimensions
    if output.tell() > 150 * 1024:
        new_dimensions = tuple(int(dim * 0.9) for dim in max_dimensions)
        img.thumbnail(new_dimensions, Image.Resampling.LANCZOS)
        output.seek(0)
        output.truncate(0)
        img.save(output, format='JPEG', quality=min_quality, optimize=True)
    
    # Nếu dung lượng dưới 100KB, tăng chất lượng để tận dụng khoảng 100-150KB
    if output.tell() < target_size_kb * 1024 and quality < max_quality:
        quality = min(quality + 10, max_quality)
        output.seek(0)
        output.truncate(0)
        img.save(output, format='JPEG', quality=quality, optimize=True)
    
    compressed_size = output.tell()
    output.seek(0)
    return output, img.size, original_size, compressed_size

# Endpoint để tải ảnh lên và nén
@app.post("/upload ảnh của bạn vào đây")
async def upload_image(
    file: UploadFile = File(...),
    max_dimension: int = Form(500),
    target_size_kb: int = Form(100)
):
    # Kiểm tra định dạng file
    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
        raise HTTPException(status_code=400, detail="Unsupported file format")
    
    # Kiểm tra tham số hợp lệ
    if max_dimension <= 0 or target_size_kb <= 0:
        raise HTTPException(status_code=400, detail="Invalid parameters")
    
    # Nén ảnh
    compressed_image, new_dimensions, original_size, compressed_size = await compress_image(
        input_image=file,
        max_dimension=max_dimension,
        target_size_kb=target_size_kb,
        min_quality=75,
        max_quality=90
    )
    
    # Tạo image_id duy nhất
    image_id = str(uuid.uuid4())
    
    # Lưu ảnh nén vào thư mục tạm
    temp_file_path = os.path.join(TEMP_DIR, f"{image_id}.jpg")
    with open(temp_file_path, "wb") as f:
        f.write(compressed_image.read())
    compressed_image.seek(0)
    
    # Lưu thông tin vào MongoDB
    image_info = {
        "image_id": image_id,
        "filename": file.filename,
        "original_size_kb": round(original_size / 1024, 2),
        "compressed_size_kb": round(compressed_size / 1024, 2),
        "new_dimensions": f"{new_dimensions[0]}x{new_dimensions[1]}",
        "temp_file_path": temp_file_path,
        "timestamp": datetime.utcnow().isoformat()
    }
    collection.insert_one(image_info)
    
    # Trả về thông tin ảnh và image_id
    return {
        "image_id": image_id,
        "filename": file.filename,
        "original_size_kb": image_info["original_size_kb"],
        "compressed_size_kb": image_info["compressed_size_kb"],
        "new_dimensions": image_info["new_dimensions"],
        "message": "Image compressed and stored successfully"
    }

# Endpoint để lấy ảnh đã nén
@app.get("/get-compressed/{image_id}")
async def get_compressed_image(image_id: str):
    # Tìm bản ghi trong MongoDB
    image_info = collection.find_one({"image_id": image_id})
    if not image_info:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Kiểm tra file ảnh nén tồn tại
    temp_file_path = image_info.get("temp_file_path")
    if not os.path.exists(temp_file_path):
        raise HTTPException(status_code=404, detail="Compressed image file not found")
    
    # Trả về ảnh nén
    return StreamingResponse(
        open(temp_file_path, "rb"),
        media_type="image/jpeg",
        headers={"Content-Disposition": f"attachment; filename=compressed_image_{image_info['new_dimensions']}.jpg"}
    )

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)