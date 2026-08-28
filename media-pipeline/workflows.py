"""ComfyUI workflow builders for the media pipeline.

All functions return API-format prompt dicts. Video paths use the
container-internal /comfy/mnt/... prefix (host run dir mounted there).
"""
import uuid

MNT = "/comfy/mnt/"  # container path prefix for the host run dir (trailing slash; concat relative paths)

NEG_IMAGE = ("low resolution, low quality, deformed, oversaturated, waxy, AI look, "
             "messy composition, blurry, watermark, text, logo")
NEG_VIDEO = ("low quality, blurry, distorted, deformed, jittery, flickering, "
             "watermark, text, logo, static camera when motion is described")


def _uid():
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------- images
def qwen_image_t2i(prompt: str, width=1280, height=720, seed=42,
                   lora="Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors",
                   steps=4, negative=NEG_IMAGE, prefix="img"):
    """Qwen-Image-2512 GGUF text-to-image with Lightning LoRA."""
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "qwen-image-2512-Q4_0.gguf"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "LoraLoader", "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": lora, "strength_model": 1.0, "strength_clip": 1.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["6", 0], "negative": ["7", 0],
                                                    "latent_image": ["4", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}},
    }


def qwen_image_edit(prompt: str, image: str, seed=42,
                    lora="Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors",
                    steps=8, prefix="img_edit"):
    """Qwen-Image-Edit-2511 GGUF image editing. `image` = ComfyUI input filename."""
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "qwen-image-edit-2511-Q4_0.gguf"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image}},
        "5": {"class_type": "LoraLoader", "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": lora, "strength_model": 1.0, "strength_clip": 1.0}},
        "6": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["5", 1], "prompt": prompt, "vae": ["3", 0], "image1": ["6", 0]}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["5", 1], "prompt": "", "vae": ["3", 0], "image1": ["6", 0]}},
        "10": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["8", 0], "reference_latents_method": "index_timestep_zero"}},
        "11": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero"}},
        "12": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["5", 0], "shift": 3.1}},
        "13": {"class_type": "CFGNorm", "inputs": {"model": ["12", 0], "strength": 1.0}},
        "14": {"class_type": "KSampler", "inputs": {"model": ["13", 0], "positive": ["10", 0], "negative": ["11", 0],
                                                     "latent_image": ["7", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                     "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": prefix}},
    }


# ---------------------------------------------------------------- video
LTXV_CKPT = "ltxv-2b-0.9.6-distilled-04-25.safetensors"
# Distilled 0.9.6 sigma schedule (8 steps, no CFG/STG needed).
LTXV_SIGMAS = "1.0000, 0.9937, 0.9875, 0.9812, 0.9750, 0.9094, 0.7250, 0.4219, 0.0"


def ltxv_i2v(keyframe: str, prompt: str, width=768, height=512, frames=97,
             fps=24.0, seed=42, negative=NEG_VIDEO, prefix="shot", strength=0.7):
    """LTXV 2B (0.9.6 distilled) image-to-video via ComfyUI checkpoint.

    `keyframe` = ComfyUI input filename. frames: 97 @24fps ~= 4.0s (must be 8k+1).
    width/height multiples of 32. Uses the distilled sigma schedule (8 steps, cfg=1).

    `strength` (LTXVImgToVideo): how strongly the keyframe anchors the clip.
    Lower = less deviation/warble. Empirically tuned 2026-08-28: 0.7 is the knee
    — erratic motion (mfd_std) drops ~80x vs 1.0 with minimal sharpness loss.
    Lower (0.5-0.6) = marginally smoother/less sharp; higher (0.8+) = more motion
    but more warble. Prompt for visual STYLE, not fast motion.
    """
    assert (frames - 1) % 8 == 0, "frames must be 8k+1"
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": LTXV_CKPT}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn.safetensors", "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "5": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": fps}},
        "6": {"class_type": "LoadImage", "inputs": {"image": keyframe}},
        "7": {"class_type": "LTXVPreprocess", "inputs": {"image": ["6", 0], "img_compression": 38}},
        "8": {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["5", 0], "negative": ["5", 1], "vae": ["1", 2],
                                                         "image": ["7", 0], "width": width, "height": height,
                                                         "length": frames, "batch_size": 1, "strength": strength}},
        "9": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["8", 0], "negative": ["8", 1], "cfg": 1.0}},
        "10": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "12": {"class_type": "ManualSigmas", "inputs": {"sigmas": LTXV_SIGMAS}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["9", 0],
                                                                 "sampler": ["10", 0], "sigmas": ["12", 0],
                                                                 "latent_image": ["8", 2]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["1", 2]}},
        "15": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["14", 0], "frame_rate": fps,
                                                             "filename_prefix": prefix, "format": "video/h264-mp4",
                                                             "pix_fmt": "yuv420p", "crf": 19, "save_metadata": False,
                                                             "save_output": True, "pingpong": False, "loop_count": 0}},
    }


def upscale_seedvr2(video_path_host: str, resolution=1080, noise_scale=0.0,
                    batch_size=5, fps=24, seed=42, prefix="upscale_b"):
    """Pipeline B: SeedVR2 3B fp8 video upscaling (temporally consistent, ~2min/5s@1080p).

    video_rel: path relative to the run dir (host /home/chuck/data/comfyui/run, container /comfy/mnt).
    """
    return {
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": f"{MNT}{video_path_host}", "force_rate": fps,
                                                            "format": "None", "skip_first_frames": 0,
                                                            "custom_width": 0, "custom_height": 0,
                                                            "frame_load_cap": 0, "select_every_nth": 1}},
        "3": {"class_type": "SeedVR2LoadVAEModel", "inputs": {"model": "ema_vae_fp16.safetensors", "device": "cuda:0",
                                                              "encode_tiled": True, "encode_tile_size": 1024,
                                                              "encode_tile_overlap": 128, "decode_tiled": True,
                                                              "decode_tile_size": 768, "decode_tile_overlap": 128,
                                                              "tile_debug": "false", "offload_device": "cpu",
                                                              "cache_model": False}},
        "4": {"class_type": "SeedVR2LoadDiTModel", "inputs": {"model": "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
                                                              "device": "cuda:0", "blocks_to_swap": 0,
                                                              "swap_io_components": False, "offload_device": "cpu",
                                                              "cache_model": False, "attention_mode": "sdpa"}},
        "5": {"class_type": "SeedVR2VideoUpscaler", "inputs": {"image": ["1", 0], "dit": ["4", 0], "vae": ["3", 0],
                                                               "seed": seed, "resolution": resolution,
                                                               "max_resolution": 0, "batch_size": batch_size,
                                                               "uniform_batch_size": True, "color_correction": "lab",
                                                               "temporal_overlap": 3, "prepend_frames": 0,
                                                               "input_noise_scale": noise_scale,
                                                               "latent_noise_scale": 0.0, "offload_device": "cpu",
                                                               "enable_debug": False}},
        "6": {"class_type": "CreateVideo", "inputs": {"images": ["5", 0], "fps": fps}},
        "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }


def upscale_ultrasharp(video_path_host: str, width=1920, height=1080, fps=24, prefix="upscale_a2"):
    """Pipeline A2: 4xUltrasharp per-frame upscale (fast, ~30s/5s, some shimmer)."""
    return {
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": f"{MNT}{video_path_host}", "force_rate": fps,
                                                            "format": "None", "skip_first_frames": 0,
                                                            "custom_width": 0, "custom_height": 0,
                                                            "frame_load_cap": 0, "select_every_nth": 1}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xUltrasharp_4xUltrasharpV10.pt"}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "upscale_method": "lanczos",
                                                      "width": width, "height": height, "crop": "disabled"}},
        "5": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["4", 0], "frame_rate": fps,
                                                           "filename_prefix": prefix, "format": "video/h264-mp4",
                                                           "pix_fmt": "yuv420p", "crf": 19, "save_metadata": False,
                                                           "save_output": True, "pingpong": False, "loop_count": 0}},
    }


def mmaudio_sfx(video_path_host: str, duration=8.0, steps=25, cfg=4.5, seed=42,
                prompt="", negative_prompt="", fps=24, prefix="sfx"):
    """MMAudio: video -> synchronized SFX/music bed (44.1kHz audio). video_rel = run-dir-relative."""
    return {
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": f"{MNT}{video_path_host}", "force_rate": fps,
                                                            "format": "None", "skip_first_frames": 0,
                                                            "custom_width": 0, "custom_height": 0,
                                                            "frame_load_cap": 0, "select_every_nth": 1}},
        "2": {"class_type": "MMAudioModelLoader", "inputs": {"mmaudio_model": "mmaudio_large_44k_v2_fp16.safetensors",
                                                             "base_precision": "fp16"}},
        "3": {"class_type": "MMAudioFeatureUtilsLoader", "inputs": {"vae_model": "mmaudio_vae_44k_fp16.safetensors",
                                                                    "synchformer_model": "mmaudio_synchformer_fp16.safetensors",
                                                                    "clip_model": "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
                                                                    "mode": "44k", "precision": "fp16"}},
        "4": {"class_type": "MMAudioSampler", "inputs": {"mmaudio_model": ["2", 0], "feature_utils": ["3", 0],
                                                          "duration": duration, "steps": steps, "cfg": cfg, "seed": seed,
                                                          "prompt": prompt, "negative_prompt": negative_prompt,
                                                          "mask_away_clip": False, "force_offload": True,
                                                          "images": ["1", 0]}},
        "5": {"class_type": "SaveAudio", "inputs": {"filename_prefix": prefix, "audio": ["4", 0]}},
    }