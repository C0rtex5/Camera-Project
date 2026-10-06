"""Create a camera profile from measured image/ground point correspondences."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.api.live_service import CameraConfig
from src.edge.calibration import fit_ground_plane
from src.edge.checkpoint import camera_fingerprint


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--survey',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--max-rmse-m',type=float,default=.15)
    args=parser.parse_args()
    if args.output.resolve() == args.report.resolve():
        parser.error('Camera profile and report must be different files')
    for target in (args.output,args.report):
        if target.exists():
            parser.error(f'{target} already exists; choose a new output to preserve it')
    survey=json.loads(args.survey.read_text())
    camera=survey['camera']
    H,report=fit_ground_plane(camera,survey['fit_points'],survey['check_points'],args.max_rmse_m)
    camera={**camera,'homography':{**camera['homography'],'H':H}}
    profile=CameraConfig.model_validate(camera).model_dump()
    report.update(camera_id=profile['camera_id'],site_id=profile['site_id'],camera_fingerprint=camera_fingerprint(profile))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    # Validation happens before either file is created. Exclusive creation avoids
    # silently replacing an existing measured calibration.
    with args.report.open('x') as target:
        json.dump(report,target,indent=2,allow_nan=False)
    with args.output.open('x') as target:
        json.dump([profile],target,indent=2,allow_nan=False)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
