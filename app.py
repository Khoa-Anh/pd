# app.py
import io
import cv2
import numpy as np
from flask import Flask, request, jsonify
from ultralytics import YOLO
import json
import os
import imutils
from flask import send_file
from image_processing import stitching_image, resize_image

# Đường dẫn đến tệp JSON chứa thông tin lớp
class_info_path = './static/data/mapping.json'

model = YOLO('../weights/best.pt')

# Đọc tệp mapping_list.json để ánh xạ class ID thành tên thật sự
with open(class_info_path, "r") as mapping_file:
    class_mapping = json.load(mapping_file)

app = Flask(__name__)

def generate_temp_filename(original_filename, counter):
    filename, file_extension = os.path.splitext(original_filename)
    return f"{filename}_{counter:02d}{file_extension}"

@app.route("/", methods=["GET"])
def index():
    return "Welcome to Object Detection API"

# Hàm nhận diện và ghép ảnh
@app.route("/objectdetection/", methods=["POST"])
def predict():
    try:
        if request.method != "POST":
            return {"error": "Invalid method"}

        if "image" not in request.files:
            return {"error": "No image in request"}

        image_files = request.files.getlist("image")

        # Kiểm tra nếu có nhiều hơn một ảnh
        if len(image_files) > 1:
            # Tạo danh sách đường dẫn ảnh từ các file nhận được
            image_paths = []
            for i, image_file in enumerate(image_files):
                image_bytes = image_file.read()
                nparr = np.frombuffer(image_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                # Tạo tên tệp tạm thời với số tiếp theo
                temp_path = generate_temp_filename(image_file.filename, i + 1)

                cv2.imwrite(temp_path, img)
                image_paths.append(temp_path)

            # Ghép ảnh lần lượt
            stitched_image = cv2.imread(image_paths[0])
            for i in range(1, len(image_paths)):
                next_image = cv2.imread(image_paths[i])
                
                matcher_type = 'affine'
                confident = 0.2
                stitching_result = stitching_image([stitched_image, next_image], matcher_type, confident)

                if "error" in stitching_result:
                    return {"error": stitching_result["error"]}
                else:
                    stitched_image = stitching_result

            # Lưu kết quả ghép ảnh vào tệp "output_stitched.jpg"
            cv2.imwrite("output_stitched.jpg", stitched_image)

            width = stitched_image.shape[1]
            height = stitched_image.shape[0]

            results = model(stitched_image, save=False, conf=0.35)

            # Sau khi hoàn thành công việc, xóa các tệp tạm thời
            for temp_path in image_paths:
                os.remove(temp_path)

        else:
            # Nếu chỉ có một ảnh, thực hiện nhận diện trực tiếp
            image_file = image_files[0]
            image_bytes = image_file.read()
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            # Resize ảnh với cạnh ngắn nhất bằng 1500
            resized_img = resize_image(img)

            width = resized_img.shape[1]
            height = resized_img.shape[0]

            # Thực hiện nhận diện trên ảnh
            results = model(resized_img, save=False, conf=0.35)

        # Xử lý kết quả và thêm vào prediction_dict
        prediction_dict = {}
        for i, result in enumerate(results):
            class_id = result.boxes.cls.tolist()
            bbox = result.boxes.xyxy.tolist()
            confidence = result.boxes.conf.tolist()

            for j in range(len(bbox)):
                class_info = next((item for item in class_mapping if item["class_id"] == int(class_id[j])), None)
                if class_info:
                    class_name = class_info["class_name"]
                    sku = class_info["sku"]
                else:
                    class_name = "Unknown"
                    sku = None
                
                x1, y1, x2, y2 = bbox[j]
                conf = round(confidence[j], 2)
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

                # Tạo một mục mới trong prediction_dict
                prediction_dict[i * len(bbox) + j] = {
                    "sku": sku,
                    "class_name": class_name,  
                    "confidence": conf,
                    "bbox": [x1, y1, x2, y2]             
                }

        # Trả về thông tin kích thước ảnh đã ghép cùng với kết quả nhận diện
        response = {
            "dimension": {
                "width": width,
                "height": height,
            },
            "results": prediction_dict
        }

        return jsonify(response)

    except Exception as e:
        error_message = "Image stitching failed. Please select appropriate images as per the instructions in the documentation."
        return {"error": error_message}

@app.route("/download", methods=["GET"])
def download_stitched_image():
    output_path = "output_stitched.jpg"
    return send_file(output_path, as_attachment=True)

# Chạy ứng dụng Flask
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
