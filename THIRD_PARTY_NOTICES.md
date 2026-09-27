# Third-party model notices

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

The bundled FFmpeg 9.0.1 build reports GPLv3 configuration because it includes GPL codecs such as x264/x265. Local execution is free. If BiliFlow or the FFmpeg binary is redistributed, the distributor must also satisfy the corresponding license, notice and source-offer obligations.
