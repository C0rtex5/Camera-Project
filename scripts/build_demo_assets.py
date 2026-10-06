"""Offline curation only: measured detections and explicitly illustrative demo geometry.

Does not train or ship production forecasting weights. Outputs bounded JPEG/JSON playback.
"""
import json
from pathlib import Path
import cv2
import numpy as np
import torch
from src.perception.detector import ConstructionSafetyDetector
from src.edge.edge_runtime import SentinelEdgeRuntime


def main():
    torch.set_num_threads(2)
    detector=ConstructionSafetyDetector(weights_path='yolov8n.pt',device='cpu',conf_threshold=.25)
    calibration={'K':[[1000,0,480],[0,1000,270],[0,0,1]],'dist':[0]*5,'H':[[25,0,480],[0,-20,540],[0,0,1]]}
    clips=[('blind_spot','Excavator Blind Spot: Worker in Hazard Zone','Worker_in_excavator_blind_spot.mp4'),
           ('near_miss','Machinery Near-Miss: Crossing Paths','Worker_near_heavy_equipment_exca9.mp4'),
           ('barrier','Controlled Co-Working: Near Barrier','Worker_and_excavator_near_barrier.mp4')]
    for key,title,filename in clips:
        output=Path('src/demo_assets')/key;output.mkdir(parents=True,exist_ok=True)
        capture=cv2.VideoCapture(str(Path('data/real_videos')/filename))
        assert capture.isOpened(),filename
        count=int(capture.get(cv2.CAP_PROP_FRAME_COUNT));fps=capture.get(cv2.CAP_PROP_FPS) or 25
        runtime=SentinelEdgeRuntime(calibration,None,mqtt_host=None,device='cpu')
        frames=[]
        for i,source_index in enumerate(np.linspace(0,count-1,min(80,count),dtype=int)):
            capture.set(cv2.CAP_PROP_POS_FRAMES,int(source_index));ok,frame=capture.read()
            if not ok:break
            frame=cv2.resize(frame,(960,540))
            detections,ppe=detector.detect(frame)
            runtime.execute_frame_cycle(detections,timestamp=float(source_index/fps))
            tracks=[]
            for track in runtime.tracks.values():
                pos=track.kf.state[:2];vel=track.kf.state[2:4]
                tracks.append({'track_id':track.track_id,'class_id':track.class_id,'position':pos.tolist(),
                  'history':[p[:2].tolist() for p in track.history[-30:]],
                  'forecast_trajectory':[(pos+vel*t).tolist() for t in np.arange(.5,3.1,.5)],'last_observed_age_seconds':0})
            frame_packet={'frame_width':960,'frame_height':540,'captured_at':float(source_index/fps),
                'detections':detections.tolist(),'ppe':ppe,'tracks':tracks,'forecast_horizon_seconds':3,
                'cycle_latency_ms':None,'debounced_alarm':'DEMO_PLAYBACK','evaluated_pairs':[],
                'hazards':[{'polygon':[[-6,5],[8,5],[8,22],[-6,22]]}],'frame_index':i}
            cv2.imwrite(str(output/f'{i:04d}.jpg'),frame,[cv2.IMWRITE_JPEG_QUALITY,72])
            frames.append(frame_packet)
        capture.release();runtime.close()
        (output/'manifest.json').write_text(json.dumps({'title':title,'fps':5,'source_recording':filename,
            'provenance':'Recorded demonstration. YOLO detections; example calibration, linear illustrative paths and example hazard geometry. No validated GNN forecast, PPE classifier or benchmark claims.',
            'frames':frames},allow_nan=False,separators=(',',':')))
        print(key,len(frames),'frames',flush=True)

if __name__=='__main__':main()
