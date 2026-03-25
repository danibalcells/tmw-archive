# CoverHunterMPS Setup

CoverHunterMPS is an Apple Silicon (MPS) port of the CoverHunter cover song
identification model. It is not on PyPI and must be cloned and set up manually.

## 1. Clone CoverHunterMPS

```bash
git clone https://github.com/alanngnet/CoverHunterMPS.git ~/CoverHunterMPS
cd ~/CoverHunterMPS
pip install -r requirements.txt
```

## 2. Obtain a pretrained checkpoint

**Option A — Original CoverHunter pretrained model (Western pop, SHS100K):**

1. Download `pt_model.zip` from:
   https://drive.google.com/file/d/1rDZ9CDInpxQUvXRLv87mr-hfDfnV7Y-j/view

2. Unzip and place the contents at:
   ```
   ~/CoverHunterMPS/training/pretrain_model/
   ```

3. Rename the inner `pt_model/` subfolder to `checkpoints/`:
   ```bash
   mv ~/CoverHunterMPS/training/pretrain_model/pt_model \
      ~/CoverHunterMPS/training/pretrain_model/checkpoints
   ```

4. Edit `~/CoverHunterMPS/training/pretrain_model/config/hparams.yaml`:
   - Change `ce:` → `foc:` on line ~67 (the pretrained checkpoint uses the old
     key name; CoverHunterMPS renamed it).

**Option B — Train your own model:**
Follow the training guide in the CoverHunterMPS README.

## 3. Configure environment variables

Add to your `.env` file at the project root:

```dotenv
# Absolute path to your CoverHunterMPS clone (added to sys.path at runtime)
COVERHUNTER_SRC_DIR=/Users/you/CoverHunterMPS

# Absolute path to the model training folder (must contain config/ and checkpoints/)
COVERHUNTER_MODEL_DIR=/Users/you/CoverHunterMPS/training/pretrain_model
```

## 4. Model hyperparameters (pretrained model)

The pretrained model was trained with these CQT parameters, which are hard-coded
in `pipeline/features/coverhunter.py`:

| Parameter        | Value  |
|------------------|--------|
| Sample rate      | 16 kHz |
| n_bins           | 96     |
| bins_per_octave  | 12     |
| fmin             | 32 Hz  |
| hop_length       | 640    |
| Embedding dim    | 128    |

The model's `chunk_frame` and `mean_size` are read from
`$COVERHUNTER_MODEL_DIR/config/hparams.yaml` at load time.

## 5. Verify setup

```bash
python -m pipeline.scripts.extract_coverhunter_embeddings --dry-run --tier 1
```
