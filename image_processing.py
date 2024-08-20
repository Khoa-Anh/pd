# image_processing.py

# Import
from stitching.image_handler import ImageHandler
from stitching.feature_detector import FeatureDetector
from stitching.feature_matcher import FeatureMatcher
from stitching.subsetter import Subsetter
from stitching.camera_estimator import CameraEstimator
from stitching.camera_adjuster import CameraAdjuster
from stitching.camera_wave_corrector import WaveCorrector
from stitching.stitching_error import StitchingError
import numpy as np
from itertools import chain
import cv2

# Hàm thực hiện việc ghép ảnh từ danh sách ảnh đầu vào
def stitching_image(imgs: list, matcher_type='affine', confident=0.2):
    """
    Ghép ảnh từ danh sách ảnh đầu vào.

    Parameters:
    - imgs (list): Danh sách các đường dẫn đến các tệp ảnh.
    - matcher_type (str): Loại matcher được sử dụng (mặc định là 'affine').
    - confident (float): Ngưỡng tin cậy để lựa chọn các cặp ảnh (mặc định là 0.2).

    Returns:
    - panorama (numpy.ndarray): Ảnh panorama đã được ghép.

    """
    # Khởi tạo đối tượng xử lý ảnh
    img_handler = ImageHandler()
    img_handler.set_img_names(imgs)

    # Resize ảnh về kích thước trung bình, thấp, và cuối cùng
    medium_imgs = list(img_handler.resize_to_medium_resolution())
    low_imgs = list(img_handler.resize_to_low_resolution(medium_imgs))
    final_imgs = list(img_handler.resize_to_final_resolution())

    # Sử dụng detector để tìm các đặc trưng trên ảnh medium
    finder = FeatureDetector(detector='brisk', thresh=10, octaves=0)
    features = [finder.detect_features(img) for img in medium_imgs]

    # Sử dụng matcher để so khớp các đặc trưng và lấy ma trận độ tin cậy
    matcher = FeatureMatcher(matcher_type)
    matches = matcher.match_features(features)
    confidence_matrix = matcher.get_confidence_matrix(matches)

    # Tạo đối tượng Subsetter để lọc các ảnh không liền kề nhau dựa trên độ tin cậy
    subsetter = Subsetter(confidence_threshold=confident)

    # Lấy chỉ số của các cặp hình có giá trị tin cậy lớn hơn ngưỡng
    row_indices, col_indices = np.where(confidence_matrix > confident)

    # Tạo danh sách các cặp hình thỏa mãn điều kiện
    selected_image_pairs = [(row, col) for row, col in zip(row_indices, col_indices)]

    # Kiểm tra và loại bỏ các cặp không liền kề nhau
    filtered_image_pairs = [pair for pair in selected_image_pairs if abs(pair[0] - pair[1]) == 1]

    # Sử dụng tập hợp để loại bỏ các cặp trùng lặp
    unique_image_pairs = set(filtered_image_pairs)

    # Trả về danh sách chỉ số cuối cùng
    indices = np.unique(np.array(list(chain.from_iterable(unique_image_pairs))))

    # Lọc danh sách ảnh theo chỉ số cuối cùng
    medium_imgs = subsetter.subset_list(medium_imgs, indices)
    low_imgs = subsetter.subset_list(low_imgs, indices)
    final_imgs = subsetter.subset_list(final_imgs, indices)
    features = subsetter.subset_list(features, indices)
    matches = subsetter.subset_matches(matches, indices)

    # Lọc danh sách tên và kích thước ảnh theo chỉ số cuối cùng
    img_names = subsetter.subset_list(img_handler.img_names, indices)
    img_sizes = subsetter.subset_list(img_handler.img_sizes, indices)
    img_handler.img_names, img_handler.img_sizes = img_names, img_sizes

    # Estimation, adjustment, và wave correction của cameras
    camera_estimator = CameraEstimator()
    camera_adjuster = CameraAdjuster(confidence_threshold=0.2)
    wave_corrector = WaveCorrector()

    try:
        cameras = camera_estimator.estimate(features, matches)
        cameras = camera_adjuster.adjust(features, matches, cameras)
        cameras = wave_corrector.correct(cameras)
    except StitchingError:
        pass  # Bỏ qua lỗi nếu có

    # Sử dụng Warper để warping các ảnh về kích thước cuối cùng
    from stitching.warper import Warper
    warper = Warper()
    warper.set_scale(cameras)
    low_sizes = img_handler.get_low_img_sizes()
    camera_aspect = img_handler.get_medium_to_low_ratio()  # do cameras được tính trên ảnh medium

    warped_low_imgs = list(warper.warp_images(low_imgs, cameras, camera_aspect))
    warped_low_masks = list(warper.create_and_warp_masks(low_sizes, cameras, camera_aspect))
    low_corners, low_sizes = warper.warp_rois(low_sizes, cameras, camera_aspect)

    final_sizes = img_handler.get_final_img_sizes()
    camera_aspect = img_handler.get_medium_to_final_ratio()  # do cameras được tính trên ảnh medium

    warped_final_imgs = list(warper.warp_images(final_imgs, cameras, camera_aspect))
    warped_final_masks = list(warper.create_and_warp_masks(final_sizes, cameras, camera_aspect))
    final_corners, final_sizes = warper.warp_rois(final_sizes, cameras, camera_aspect)

    # Sử dụng Cropper để cắt ảnh
    from stitching.cropper import Cropper
    cropper = Cropper()
    low_corners = cropper.get_zero_center_corners(low_corners)
    cropper.prepare(warped_low_imgs, warped_low_masks, low_corners, low_sizes)

    cropped_low_masks = list(cropper.crop_images(warped_low_masks))
    cropped_low_imgs = list(cropper.crop_images(warped_low_imgs))
    low_corners, low_sizes = cropper.crop_rois(low_corners, low_sizes)

    lir_aspect = img_handler.get_low_to_final_ratio()  # do lir được tính trên ảnh low
    cropped_final_masks = list(cropper.crop_images(warped_final_masks, lir_aspect))
    cropped_final_imgs = list(cropper.crop_images(warped_final_imgs, lir_aspect))
    final_corners, final_sizes = cropper.crop_rois(final_corners, final_sizes, lir_aspect)

    # Sử dụng SeamFinder để tìm các đường nối giữa các ảnh
    from stitching.seam_finder import SeamFinder
    seam_finder = SeamFinder()
    seam_masks = seam_finder.find(cropped_low_imgs, low_corners, cropped_low_masks)
    seam_masks = [seam_finder.resize(seam_mask, mask) for seam_mask, mask in zip(seam_masks, cropped_final_masks)]

    # Sử dụng ExposureErrorCompensator để làm mờ độ chênh lệch sáng
    from stitching.exposure_error_compensator import ExposureErrorCompensator
    compensator = ExposureErrorCompensator()
    compensator.feed(low_corners, cropped_low_imgs, cropped_low_masks)
    compensated_imgs = [compensator.apply(idx, corner, img, mask) 
                        for idx, (img, mask, corner) 
                        in enumerate(zip(cropped_final_imgs, cropped_final_masks, final_corners))]

    # Sử dụng Blender để ghép các ảnh lại với nhau
    from stitching.blender import Blender
    blender = Blender()
    blender.prepare(final_corners, final_sizes)
    for img, mask, corner in zip(compensated_imgs, seam_masks, final_corners):
        blender.feed(img, mask, corner)

    # Trả về ảnh panorama đã được ghép
    panorama, _ = blender.blend()
    return panorama

# Hàm resize
def resize_image(image, target_size=1500):
    # Lấy kích thước của ảnh
    h, w = image.shape[:2]

    # Xác định cạnh ngắn nhất và chiều của ảnh mới
    min_edge = min(h, w)
    new_h, new_w = (target_size, int(w * (target_size / min_edge))) if h > w else (int(h * (target_size / min_edge)), target_size)

    # Resize ảnh
    resized_image = cv2.resize(image, (new_w, new_h))

    return resized_image