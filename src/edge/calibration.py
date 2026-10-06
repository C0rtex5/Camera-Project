"""Fit a camera's ground-plane calibration and verify independent survey points."""
import cv2
import numpy as np

from src.edge.homography import HomographyProjector


def fit_ground_plane(camera, fit_points, check_points, max_rmse_m=.15):
    """Return H (metric ground -> undistorted pixels) and measured error report.

    K/dist must come from a separate intrinsic calibration for the exact stream.
    Check points are not used in the fit and must be distinct from fit points.
    """
    if not np.isfinite(max_rmse_m) or max_rmse_m <= 0:
        raise ValueError('Maximum calibration RMSE must be positive and finite')
    K = np.asarray(camera['homography']['K'], dtype=np.float64)
    dist = np.asarray(camera['homography']['dist'], dtype=np.float64)
    if K.shape != (3,3) or not np.isfinite(K).all() or abs(np.linalg.det(K)) < 1e-12:
        raise ValueError('Provide valid intrinsic calibration K')
    if dist.size not in (4,5,8,12,14) or not np.isfinite(dist).all():
        raise ValueError('Provide valid lens distortion coefficients')
    def points(records, label):
        pixel = np.asarray([r['pixel'] for r in records], dtype=np.float64)
        ground = np.asarray([r['ground'] for r in records], dtype=np.float64)
        if len(records) < 4 or pixel.shape != (len(records),2) or ground.shape != pixel.shape:
            raise ValueError(f'{label}: provide at least four pixel/ground coordinate pairs')
        if not np.isfinite(pixel).all() or not np.isfinite(ground).all():
            raise ValueError(f'{label}: coordinates must be finite')
        if np.any(pixel < 0) or np.any(pixel[:,0] >= camera['width']) or np.any(pixel[:,1] >= camera['height']):
            raise ValueError(f'{label}: pixel coordinates are outside the configured stream resolution')
        if len(np.unique(ground,axis=0)) != len(ground) or len(np.unique(pixel,axis=0)) != len(pixel):
            raise ValueError(f'{label}: repeated survey points are not independent constraints')
        if np.linalg.matrix_rank(np.column_stack((ground,np.ones(len(ground))))) < 3:
            raise ValueError(f'{label}: ground points must not be collinear')
        return pixel,ground
    fit_pixel,fit_ground = points(fit_points,'fit_points')
    check_pixel,check_ground = points(check_points,'check_points')
    if np.any(np.linalg.norm(fit_ground[:,None,:]-check_ground[None,:,:],axis=2) < 1e-6):
        raise ValueError('Check points must be different surveyed locations from fit points')
    undistorted = cv2.undistortPoints(fit_pixel.reshape(-1,1,2),K,dist,P=K).reshape(-1,2)
    H,_ = cv2.findHomography(fit_ground,undistorted,method=0)
    if H is None or not np.isfinite(H).all() or abs(np.linalg.det(H)) < 1e-12:
        raise ValueError('Survey points do not define an invertible ground-plane calibration')
    projector = HomographyProjector(K,dist,H)
    fit_errors = np.linalg.norm(projector.pixel_to_metric(fit_pixel)-fit_ground,axis=1)
    check_errors = np.linalg.norm(projector.pixel_to_metric(check_pixel)-check_ground,axis=1)
    rmse = float(np.sqrt(np.mean(check_errors**2)))
    if not np.isfinite(rmse) or rmse > max_rmse_m:
        raise ValueError(f'Independent calibration RMSE {rmse:.4f} m exceeds {max_rmse_m:.4f} m')
    return H.tolist(), {'fit_points':len(fit_points),'check_points':len(check_points),
                        'fit_rmse_m':float(np.sqrt(np.mean(fit_errors**2))),
                        'check_rmse_m':rmse,'check_max_error_m':float(check_errors.max()),
                        'check_errors_m':check_errors.tolist(),'max_rmse_m':max_rmse_m}
