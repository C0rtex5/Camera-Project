# SEC-01 — PoC camera survey procedure

This procedure identifies plant cameras that can be used for the proximity-detection PoC and produces a documented, credential-free validation record.

## 1. Obtain the candidate list

The application does not scan the plant network. Ask the site/IT owner for the candidate camera ID, physical location, IP/host, RTSP port, stream path, and access path from the application host. Do not put camera passwords in the endpoint or in chat. For a camera reached through the test server, record the server-side RTSP endpoint and the optional SSH tunnel separately.

## 2. Validate each candidate

1. Open `/setup` on the application host.
2. Enter the candidate camera ID, physical location, IP/host, RTSP port, stream path, and access path (`direct`, `vpn`, or `ssh-tunnel`). The app shows the derived endpoint before it probes it.
3. Enter the camera username/password only in the private form fields. For an SSH route, also enter the SSH destination and the key-file/known-hosts paths.
4. Choose **Validate current camera and log survey**.
5. Review the result. A successful result includes the frame resolution and objective measurements for brightness, contrast, sharpness, and screening notes.
6. If the result is blocked, record the structured blocker code and give it to IT/network ownership. Common codes include `camera_authentication`, `stream_not_found`, `connection_refused`, `connection_timeout`, and `ssh_host_key`.
7. Repeat for every candidate. Each attempt is retained in the private survey file.

The survey reads one frame only. It does not start live inference, download recordings, or require calibration/model files.

For an IT-provided candidate list, the same workflow is available as a bounded batch API (maximum 20 candidates):

```sh
curl -X POST http://127.0.0.1:18080/api/v1/setup/survey/batch \
  -H 'Content-Type: application/json' \
  -d '{"candidates":[{"camera_id":"CAM-01","location":"North Yard / Gate 3","host":"192.168.10.21","rtsp_port":554,"stream_path":"Streaming/Channels/101","access_path":"direct"}]}'
```

Valid candidates are recorded individually. Invalid or incomplete candidates are returned in an `errors` list without exposing submitted credential values. The setup page also exposes this as **Validate an IT-provided candidate list (JSON)**. The batch endpoint is sequential and uses the same one-frame probe as the setup button.

For a blocked attempt, record the network-owner disposition with:

```text
POST /api/v1/setup/survey/disposition
```

Use `status` values `open`, `reviewed`, `approved`, `rejected`, or `deferred`, plus a short note. The setup page marks blocked attempts as reviewed; the report retains the disposition and note.

## 3. Select and document the PoC camera

After a candidate returns a frame, its quality notes have been reviewed, and its physical location, IP/host, RTSP port, and stream path are recorded, choose **Select for PoC**. A frame alone is not enough to document the camera inventory. Download or save the redacted report from:

```text
/api/v1/setup/survey/report.md
/api/v1/setup/survey/report.json
```

Attach the report to the PoC record together with:

- selected camera ID and site/area;
- RTSP host/port and stream path, without credentials or query tokens;
- measured resolution;
- quality measurements and any review notes;
- network-owner disposition of every logged block;
- date, application version, and operator who performed the check.

The report deliberately excludes passwords, private keys, stream query strings, and video.

## Screening thresholds

The current PoC screening values are:

| Measurement | Screening value |
|---|---:|
| Minimum width | 640 px |
| Minimum height | 480 px |
| Dark-frame mean luma | below 20 |
| Over-bright mean luma | above 235 |
| Laplacian sharpness | below 50 |
| Contrast standard deviation | below 15 |

A quality status of `review` is a request for human/site review, not an automatic rejection. A camera must not be considered production-validated solely because it passes these heuristics. Site lighting, occlusion, frame rate, calibration suitability, detection performance, alert latency, and network stability require separate validation.

## Data handling and acceptance

Survey records are stored in the application data volume at `data/surveys/camera_survey.json` with mode `0600`. The file is excluded from Git and from the Docker build context. Back up the volume with the same controls as the rest of the private runtime data.

SEC-01 is complete for a site only when at least one candidate has a logged successful frame result, its resolution and quality evidence have been reviewed, every connectivity block has a documented disposition, and the selected-camera report has been retained. Without the plant camera list and network-owner inputs, the application can provide the workflow and evidence store but cannot honestly mark a physical camera as selected or validated.
