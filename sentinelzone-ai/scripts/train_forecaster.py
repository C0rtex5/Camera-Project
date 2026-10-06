"""Train a camera-aligned GNN candidate from reviewed metric trajectory windows.

Each NPZ contains node_history[N,30,8], class_ids[N], edge_index[2,E],
edge_attr[E,5], and future_xy[N,50,2]. Split by recording, not overlapping windows.
This script never trains on its own predicted future positions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.api.live_service import CameraConfig
from src.edge.checkpoint import camera_fingerprint, model_contract
from src.edge.gatv2_model import WorkZoneSTGNN


def load_sample(path, device):
    with np.load(path, allow_pickle=False) as archive:
        data = {key: archive[key] for key in ('node_history', 'class_ids', 'edge_index', 'edge_attr', 'future_xy')}
    n = len(data['class_ids'])
    edges = data['edge_index']
    if n < 2 or data['node_history'].shape != (n, 30, 8) or data['future_xy'].shape != (n, 50, 2):
        raise ValueError(f'{path.name}: invalid observation/target dimensions')
    if edges.ndim != 2 or edges.shape[0] != 2 or edges.shape[1] < 1 or data['edge_attr'].shape != (edges.shape[1], 5):
        raise ValueError(f'{path.name}: invalid graph dimensions')
    if not np.issubdtype(edges.dtype, np.integer) or edges.min() < 0 or edges.max() >= n:
        raise ValueError(f'{path.name}: invalid edge indices')
    if not np.issubdtype(data['class_ids'].dtype, np.integer) or not np.isin(data['class_ids'], [0, 1, 2, 3]).all():
        raise ValueError(f'{path.name}: invalid class IDs')
    if not all(np.isfinite(value).all() for value in data.values()):
        raise ValueError(f'{path.name}: nonfinite training data')
    return {key: torch.as_tensor(value, dtype=torch.long if key in ('class_ids', 'edge_index') else torch.float32, device=device) for key, value in data.items()}


def infer(model, sample):
    return model(sample['node_history'], sample['class_ids'], sample['edge_index'], sample['edge_attr'])


def trajectory_loss(pred, target):
    # Diagonal/correlated bivariate Gaussian mixture, averaged across future steps.
    sx, sy = pred['sigma_x'].clamp(.05, 100), pred['sigma_y'].clamp(.05, 100)
    rho = pred['rho'].clamp(-.95, .95)
    dx = (target[:, None, :, 0] - pred['mu_x']) / sx
    dy = (target[:, None, :, 1] - pred['mu_y']) / sy
    variance = 1 - rho.square()
    log_density = -torch.log(2 * torch.pi * sx * sy * variance.sqrt()) - (dx.square() + dy.square() - 2*rho*dx*dy) / (2*variance)
    mode_log_prob = torch.log(pred['mode_probs'].clamp_min(1e-9))
    return -torch.logsumexp(mode_log_prob + log_density.mean(dim=-1), dim=1).mean()


def train(args):
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    profile = CameraConfig.model_validate(json.loads(args.camera_config.read_text())[0]).model_dump()
    fingerprint = camera_fingerprint(profile)
    dataset_metadata = json.loads((args.dataset / 'metadata.json').read_text())
    if dataset_metadata.get('camera_fingerprint') != fingerprint:
        raise ValueError('Dataset camera fingerprint does not match the configured camera')
    if dataset_metadata.get('sample_interval_seconds') != .1:
        raise ValueError('Training windows must be sampled at 10 Hz')
    training = sorted((args.dataset / 'train').glob('*.npz'))
    validation = sorted((args.dataset / 'validation').glob('*.npz'))
    if not training or not validation:
        raise ValueError('Provide separate nonempty training and validation recordings')
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    train_hashes = {digest(p) for p in training}
    val_hashes = {digest(p) for p in validation}
    if train_hashes & val_hashes:
        raise ValueError('Identical windows occur in both training and validation data')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = WorkZoneSTGNN().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    best = float('inf')
    for epoch in range(args.epochs):
        model.train()
        random.shuffle(training)
        losses = []
        for path in training:
            sample = load_sample(path, device)
            optimizer.zero_grad()
            loss = trajectory_loss(infer(model, sample), sample['future_xy'])
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite training loss; checkpoint was not updated')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(loss.item())
        model.eval()
        ade, fde = [], []
        with torch.no_grad():
            for path in validation:
                sample = load_sample(path, device)
                pred = infer(model, sample)
                mode = pred['mode_probs'].argmax(dim=1)
                rows = torch.arange(len(mode), device=device)
                xy = torch.stack((pred['mu_x'][rows,mode], pred['mu_y'][rows,mode]), dim=-1)
                errors = torch.linalg.vector_norm(xy - sample['future_xy'], dim=-1)
                ade.extend(errors.mean(dim=1).cpu().tolist())
                fde.extend(errors[:,-1].cpu().tolist())
        score = float(np.mean(ade))
        if not np.isfinite(score):
            raise ValueError('Validation did not produce finite metrics')
        if score < best:
            best = score
            metadata = {**model_contract(), 'camera_fingerprint': fingerprint,
                        'dataset_sha256': hashlib.sha256(''.join(sorted(train_hashes | val_hashes)).encode()).hexdigest(),
                        'validation': {'ade_m': score, 'fde_m': float(np.mean(fde)), 'samples': len(validation)},
                        'training': {'epoch': epoch+1, 'seed': args.seed, 'samples': len(training)}}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temp = args.output.with_suffix('.tmp')
            torch.save({'model_state_dict': model.state_dict(), 'metadata': metadata}, temp)
            temp.replace(args.output)
        print(json.dumps({'epoch': epoch+1, 'loss': float(np.mean(losses)), 'validation_ade_m': score}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--camera-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--learning-rate', type=float, default=1e-3)
    parser.add_argument('--cpu-threads', type=int, default=2)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.epochs < 1 or args.cpu_threads < 1 or args.learning_rate <= 0:
        parser.error('epochs, cpu-threads and learning-rate must be positive')
    train(args)


if __name__ == '__main__':
    main()
