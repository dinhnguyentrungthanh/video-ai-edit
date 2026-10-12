# Third-party notices

BiliFlow stores third-party model weights locally. Their upstream terms remain in force.

| Component | License | Source |
|---|---|---|
| EasyOCR models | Apache-2.0 | https://github.com/JaidedAI/EasyOCR |
| viddexa/nsfw-detection-2-nano | Apache-2.0 | https://huggingface.co/viddexa/nsfw-detection-2-nano |
| OwenElliott/image-safety-classifier-m | MIT | https://huggingface.co/OwenElliott/image-safety-classifier-m |
| SmilingWolf/wd-vit-tagger-v3 | Apache-2.0 | https://huggingface.co/SmilingWolf/wd-vit-tagger-v3 |
| MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli | MIT | https://huggingface.co/MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli |
| jaranohaal/vit-base-violence-detection | Apache-2.0 | https://huggingface.co/jaranohaal/vit-base-violence-detection |
| Qwen/Qwen2-VL-2B-Instruct | Apache-2.0 | https://huggingface.co/Qwen/Qwen2-VL-2B-Instruct |
| microsoft/Florence-2-base | MIT | https://huggingface.co/microsoft/Florence-2-base |
| IDEA-Research/grounding-dino-tiny | Apache-2.0 | https://huggingface.co/IDEA-Research/grounding-dino-tiny |
| google/siglip-base-patch16-224 | Apache-2.0; benchmarked then weights removed after failing the quality gate | https://huggingface.co/google/siglip-base-patch16-224 |
| PaddleOCR 3.7.0 / PaddlePaddle 3.2.0 | Apache-2.0; benchmarked then removed because it was slower than EasyOCR | https://github.com/PaddlePaddle/PaddleOCR |
| microsoft/Phi-3.5-vision-instruct-onnx INT4 | MIT; benchmarked then removed for insufficient accuracy | https://huggingface.co/microsoft/Phi-3.5-vision-instruct-onnx |
| KingTechnician VideoMAE XD violence model | CC-BY-NC-4.0; retired and removed | https://huggingface.co/KingTechnician/videomae-small-finetuned-kinetics-xd-violence-binary |

The exact downloaded revisions and SHA-256 checksums are recorded in each model directory's `manifest.json`.

## Runtime components

The local runtime also uses free/open-source packages. Direct dependencies include Apache-2.0 components (EasyOCR, Hugging Face Hub, safetensors, timm, Transformers), MIT components (PyYAML), BSD-3-Clause components (protobuf), Apache-2.0 components (SentencePiece), BSD components (psutil and NVIDIA's Python NVML binding), MIT-CMU Pillow, and PyTorch's published permissive license expression. Exact installed versions remain locked in the local environment and `requirements.lock.txt`.

## Video download tools

The "Tải video" feature runs these local tools. Exact versions, licenses, sources and the Deno SHA-256 are pinned in `config/download_tools.json` and checked by `python -m biliflow.download_tools audit`.

| Component | License | Source |
|---|---|---|
| yt-dlp 2026.8.19 (PyPI wheel, run as `python -m yt_dlp`) | Unlicense | https://pypi.org/project/yt-dlp/ |
| yt-dlp-ejs 0.8.0 | Unlicense AND MIT AND ISC | https://pypi.org/project/yt-dlp-ejs/ |
| Deno 2.9.7 (`tools\deno\deno.exe`) | MIT | https://github.com/denoland/deno |
| requests, urllib3, charset-normalizer, certifi, idna, brotli, websockets, pycryptodomex | Apache-2.0, MIT, MIT, MPL-2.0, BSD-3-Clause, MIT, BSD-3-Clause, BSD-2-Clause AND public domain | PyPI |

The standalone `yt-dlp.exe` build bundles GPLv3+ code and is not used. `mutagen` (GPL-2.0-or-later) and `curl_cffi` are not installed.

The bundled FFmpeg 9.0.1 build reports GPLv3 configuration because it includes GPL codecs such as x264/x265. Local execution is free. If BiliFlow or the FFmpeg binary is redistributed, the distributor must also satisfy the corresponding license, notice and source-offer obligations.

## Components shipped by the installer

The installer downloads or ships the components below. Each keeps its own license; their full license texts and NOTICE files are copied into `<install>\licenses\` at install time.

| Component | License | Notes |
|---|---|---|
| CPython runtime (`runtime\python`) | PSF-2.0 (bundled OpenSSL Apache-2.0, others per its notices) | python-build-standalone build installed by uv |
| PyTorch, torchvision (cu126 or CPU build) | BSD-3-Clause | The CUDA build includes NVIDIA CUDA runtime libraries under the NVIDIA CUDA Toolkit EULA (redistributable runtime components) |
| Playwright for Python (with its bundled Node.js driver) | Apache-2.0 (Node.js: MIT and its own notices) | Uses the Microsoft Edge already installed in Windows; no browser is shipped |
| pycryptodomex | BSD-2-Clause AND public domain | Also verifies activation keys (Ed25519) |
| Nuitka runtime parts in compiled modules | Apache-2.0 | Build tool; only its runtime helpers end up in the compiled modules |
| Inno Setup (installer engine) | Inno Setup License | Free, including commercial use; origin must not be misrepresented |
| Deno | MIT | |
| FFmpeg | GPL-3.0-or-later (build configured with GPL codecs such as x264/x265) | Downloaded at install time from the builder's official release page, version and SHA-256 pinned; see "FFmpeg" below |

Model weights keep the licenses in the table at the top of this file. For each Apache-2.0 model, the installer copies the upstream `LICENSE` and, when the model repository has one, its `NOTICE` file next to the weights. For each MIT model, it copies the upstream copyright notice and license text.

## FFmpeg

BiliFlow runs `ffmpeg.exe` and `ffprobe.exe` as separate programs; it does not link FFmpeg. The installer does not ship FFmpeg: at install time it downloads the FFmpeg build below from the builder's official release page and checks its pinned version and SHA-256 before use. The build is configured with `--enable-gpl --enable-version3` and without `--enable-nonfree`; the installer refuses a nonfree build.

| Build | Builder | License | Source code |
|---|---|---|---|
| FFmpeg 9.0.1 `essentials_build`, 64-bit static | gyan.dev | GPL-3.0-or-later | FFmpeg: https://github.com/FFmpeg/FFmpeg/commit/bf1b838f2a ; the versions of the external libraries in the build (x264, x265 and others) are listed in the build's `README.txt` |

The build's `LICENSE` and `README.txt` are kept next to it in `<install>\tools\ffmpeg\`. FFmpeg is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 3 or later.
