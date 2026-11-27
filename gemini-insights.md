Creative director Bruno Zordan and collaborator Lucas Heck create their viral YouTube movies on the One Minute with Ai channel by combining 20+ years of traditional AAA game and entertainment direction with an iterative, multi-tool generative AI pipeline.
Rather than relying on a single "one-click" AI prompt, their production—best known for their ~22-minute live-action fan adaptations of the 1980s Dungeons & Dragons (Caverna dos Dragões) cartoon—functions like a compressed studio workflow:

1. Creative Direction & Pre-Production

• Veteran Art Direction. Bruno Zordan is a Chicago-based creative director who previously spent over seven years at NetherRealm Studios launching Mortal Kombat 11, Mortal Kombat 1, and Injustice 2, alongside roles at Cartoon Network. He and Lucas Heck treat AI tools as a virtual film crew—applying professional pacing, shot composition, lighting, and storytelling fundamentals rather than letting the AI dictate the narrative.
• Passion-First Scripting. They specifically target nostalgic, niche fandoms (such as giving the classic D&D animated series a gritty, live-action conclusion) where traditional Hollywood budgets wouldn't make financial sense.

2. Visual & Video Generation Pipeline

• Integrated AI Creation Platforms. Zordan has showcased using all-in-one AI creative suites like SuperCool to rapidly prototype and generate complex action sequences (such as cyberpunk gunfights with muzzle flashes and armored choreography) directly from text and visual prompts.
• Consistent Character & World Anchoring. To keep characters like Hank, Presto, and Venger recognizable across a 20+ minute episode without the "morphing" common in raw AI video, AI filmmakers in this tier lock in visual continuity using dedicated character reference sheets, consistent lighting/color-grade prompts, and image-to-video generation (generating keyframe stills first, then animating them in short 4-to-10 second clips).

3. Audio, Pacing & Traditional Post-Production

• Editorial Assembly. The biggest differentiator in their Dungeons & Dragons episodes is traditional video editing. Generating dozens of short AI clips requires heavy curation—discarding flawed takes, cutting on action to hide minor AI artifacts, and maintaining tight dramatic pacing.
• Voice & Sound Design. Dialogue, lip-syncing, sound effects, and cinematic scores are layered in post-production using AI voice synthesis/acting tools and traditional non-linear editors (like Premiere Pro or DaVinci Resolve) to give the final cut theatrical weight.
(Note: While Zordan has not published a full, exhaustive software list for the Dungeons & Dragons episodes specifically, he regularly shares prototypes and workflow breakdowns across his LinkedIn profile and YouTube descriptions.)
If you're looking to build a similar workflow, let me know:
• Whether you want to run it locally on your own GPU/Mac or via cloud tools
• Your target visual style (live-action realism, 2D anime, or 3D)
I can break down the exact open-source or commercial stack to match.

--- continued discussion ---

Your current architecture—a shot plan, a draft model, a final model, post-audio, and FFmpeg stitching—is actually the exact structural backbone of professional AI filmmaking.
The reason channels like One Minute with Ai achieve that cohesive, live-action realism isn't a secret all-in-one generator. It comes down to two major shifts in how they handle the keyframes before video generation and pacing after generation, plus selectively offloading the hardest shots to cloud models when local VRAM hits a ceiling.

1. The Missing Link: Keyframe-First & "Element" Anchoring

If you are going straight from a text prompt to your video model (even with a draft-to-final pass), you will fight constant character drift and lighting shifts. Live-action AI filmmakers decouple visual consistency from motion:
• Character & Location Sheets (LookDev): Before generating a single frame of video, generate high-resolution "turnaround" sheets (front, side, 3/4, close-up) for every character and establishing shots for every environment on neutral lighting.
• Multi-Reference Keyframing: Instead of prompting a video model from scratch, generate the exact starting frame (and sometimes ending frame) of every shot in your shot plan as a static image first. Feed your character sheet + location sheet into an image model with multi-reference or ControlNet support (like Flux Dev/Kontext or Qwen-Image-Edit, which run comfortably in 24GB VRAM).
• Image-to-Video (I2V) Only: Once your static keyframes look like stills from a real movie—with locked faces, wardrobe, and color grading—feed them into your video model using Image-to-Video (or First/Last Frame interpolation). Because the model doesn't have to invent the face or the set, 100% of its compute goes into realistic physics and camera movement.

2. Where Your 24GB RTX 4000 Shines vs. Where Cloud APIs Win

With 24GB of VRAM (such as an RTX 4000 Ada or Blackwell), you have plenty of headroom for quantized local models, especially if you use block-swapping/offloading in ComfyUI. However, top YouTube creators use a hybrid approach:

Pipeline Stage	Best Local Approach (24GB VRAM)	When to Offload to Cloud APIs
Keyframe Generation & Inpainting	Flux.1 Dev (FP8) + ControlNet / IP-Adapter, or Qwen-Image for multi-reference character consistency.	Rarely needed—local 24GB handles high-res stills faster and with better control.
Draft & Blocking Pass	LTX-Video or Wan 2.2 (5B / 14B Q4/Q5) at 480p–540p. Great for testing camera moves and timing in seconds.	Not needed—keep iteration free and instant locally.
Final Live-Action Video	Wan 2.2 (14B MoE) or HunyuanVideo (FP8 with SageAttention + TeaCache) upscaled to 1080p.	Complex human action, fight choreography, or multi-character dialogue. Cloud models (Kling 2.x, Veo 3, Runway Gen-4) still beat local quantized models on human micro-expressions and physics.
Dialogue & Lip-Sync	LatentSync or MuseTalk applied after video generation on medium/close-up shots.	Native audio-visual models (like Veo 3 or Kling) when a character needs emotional acting synced directly to voice delivery.


3. Why FFmpeg Auto-Stitching Feels "AI-Generated" (Editorial Pacing)

Blindly stitching clips together sequentially with FFmpeg is the #1 reason automated pipelines feel stiff. High-end AI films feel real because of micro-editing:
• Trim the "AI Ramp-Up": Almost every diffusion video model takes 0.5 to 1 second to settle into natural motion, and often degrades in the final second. Instead of stitching raw 5-second outputs end-to-end, trim the first ~12 frames and last ~12 frames in your FFmpeg filtergraph (trim=start_frame=12:end_frame=108), keeping only the sharpest 2–3 seconds of peak motion.
• Cut on Action & L-Cuts: Have your audio track dictate the cuts rather than letting every video clip play to completion. Overlap dialogue across cuts (J-cuts and L-cuts) so the viewer's brain bridges the visual transition between two separate AI generations.
• Unify the Film Grain & Grade: In your final FFmpeg pass (or DaVinci Resolve), apply a single 3D LUT, subtle halation, and uniform 35mm film grain across the entire concatenated timeline. This glues clips generated at different times into a single cohesive camera sensor look.

Quick Upgrades You Can Add to Your Existing Pipeline

1. Add a Keyframe Node Before Video: Update your shot-plan schema so each shot outputs a keyframe_prompt (referencing character/set asset IDs) and a motion_prompt (describing only camera movement and action, not appearance).
2. Swap Raw Concat for Frame-Trimmed Concat: Update your FFmpeg script to trim dead frames at the head/tail of clips and inject a uniform grain/color pass at the end.
3. Add a Hybrid API Escape Hatch: Tag complex shots in your shot plan (e.g., "complexity": "high" for fight scenes or close-up dialogue) so your pipeline routes 80% of B-roll and establishing shots to your local RTX 4000, and sends the 20% hero shots to a cloud API.
If you want to refine your existing setup, let me know:
• Which specific draft and final models (and backend like ComfyUI or Diffusers) your pipeline currently runs
• Where your biggest quality bottleneck is right now (character consistency, realistic motion, or lip-sync/dialogue)
I can suggest specific node workflows or FFmpeg filter adjustments to upgrade it.

--- continued follow up with gemini ----
Yes—introducing prescriptive storyboards and breaking action into 1-to-2-second micro-shots is the exact secret to making local models like Wan 2.2 (14B) and LTX 2.5 look like real cinema.
AI video models fall apart when asked to generate multi-beat choreography (e.g., "they clash blades, spin, and parry"). By keeping your stack 100% local on your 24GB RTX 4000, you can solve both character consistency and weird fight physics by changing how your pipeline stages the shots before Wan 2.2 ever sees them.

1. Fixing Fight Physics: The "1.5-Second Rule" & Action Chaining

In real action cinema (think John Wick or Bourne), complex fights are never single long takes—they are rapid cuts of single physical beats.
• One Physical Beat Per Clip (25–49 Frames): Limit your Wan 2.2 / LTX 2.5 generation length to 1.5 to 2 seconds (33 to 49 frames at 24fps). Instead of prompting "Mr. Potato Head and Dark Broccoli have a lightsaber battle," break it into four micro-shots:
	• Shot A (1.5s): Close-up on Mr. Potato Head's plastic eyes narrowing; he ignites a glowing blue saber upward.
	• Shot B (1.5s): Low-angle medium shot of Dark Broccoli lunging forward, swinging a red saber diagonally down-left.
	• Shot C (1.0s): Tight close-up on the two blades colliding in a shower of bright sparks, camera shakes violently.
	• Shot D (1.5s): Over-the-shoulder shot of Mr. Potato Head stumbling backward onto the kitchen counter.
• Frame-Chaining (Last-Frame to First-Frame): To keep continuity across fast cuts without full-scene drift, extract the exact last clean frame of Shot A (using an ImageFromBatch node in ComfyUI) and feed it directly as the starting image (start_image) for Shot B.
• First-Last-Frame Interpolation (FLF2V): For complex poses where Wan 2.2 guesses wrong mid-swing, generate both the start frame and the end frame in Qwen-Image 2 first, then use Wan 2.2’s First-Last-Frame conditioning. When the model knows both where the saber starts and where it lands, the physics in between stay rock-solid.

2. Locking Character Consistency in ComfyUI

Since you are already running Qwen-Image 2 (which excels at multi-image reference editing and spatial reasoning), use it as a Virtual Art Department before video generation:
• Build a "Contact Sheet" Anchor: Have Qwen-Image 2 generate a single 3-angle reference sheet (Front, 3/4 Profile, Back) on a neutral gray background for every character. Save these as permanent project assets.
• Use Qwen-Image 2 as a "Set Photographer" (Multi-Reference): Never rely on text alone to describe Mr. Potato Head or Dark Broccoli in a shot. Pass three inputs into Qwen-Image 2 for every storyboard frame:
	1. Character A reference sheet
	2. Character B reference sheet
	3. Empty background/set plate (e.g., "dimly lit moody kitchen counter at night, shallow depth of field")

	• Prompt Qwen-Image 2 to composite them into the exact camera angle and pose for that 1.5-second beat.
• Color-Match Before Video: Run a ColorMatch node in ComfyUI comparing your newly generated storyboard frame against your master scene plate before feeding it to Wan 2.2. This stops lighting and contrast from flickering between cuts.

3. Upgrading Your LLM Storyboarder (Gemini 31B)

Since your local LLM writes the script and shot plan, make its JSON schema strictly prescriptive so it acts like a Director of Photography rather than a screenwriter.
Have it output separate prompts for your Image Node (static composition) and your Video Node (pure physics/camera motion):

JSON Field	Target Node	What to Enforce in the System Prompt
shot_duration_frames	Wan 2.2 / LTX 2.5	Hard-cap action shots at 33 or 49 frames; allow 81–121 frames only for slow dialogue or establishing pans.
storyboard_prompt	Qwen-Image 2	Zero motion verbs. Describe static pose, exact lens (35mm anamorphic, 85mm macro), lighting direction, and spatial placement (left third, foreground).
motion_prompt	Wan 2.2 (14B)	Zero appearance words. Do not re-describe what the characters look like (Qwen-Image 2 already drew them). Describe only velocity, physics, and camera (handheld whip-pan right, sparks flying, fast downward slash).
cut_type	FFmpeg Stitcher	Tag as hard_cut (for fight impacts) or crossfade_4f (for smooth motion continuation).


4. Practical ComfyUI Optimizations for 24GB VRAM

• Draft with LTX 2.5, Hero with Wan 2.2 (14B): Keep using LTX 2.5 at low resolution (512x288 or 640x360) to preview your 1.5-second fight timing in seconds. Once the pacing works in FFmpeg, batch-render the final 49-frame clips through Wan 2.2 14B (FP8/GGUF Q5) with SageAttention and TeaCache enabled—at 49 frames, 24GB VRAM will fly through them without heavy block-swapping bottlenecks.
• RIFE Frame Interpolation Post-Pass: Because 33-to-49-frame clips can feel rushed if an impact lands too fast, run a lightweight RIFE node in ComfyUI (or minterpolate in FFmpeg) on select impact shots to slow them down by 20–30% for dramatic weight.


---- continued discussion ----
Yes—you nailed the hierarchy. Separating the narrative beat (the Storyboard) from the micro-camera choreography (the Shot List) while anchoring both to locked Character & Environment Sheets is how automated pipelines jump from "AI montage" to actual filmmaking.
To make that two-tier architecture bulletproof on your local RTX 4000, add these four production tricks to your pipeline:

1. Enforce the "180-Degree Rule" in Your Shot List

When your LLM breaks a single fight beat into five 1.5-second micro-shots, AI models love to accidentally flip characters to opposite sides of the frame between cuts—which makes the viewer instantly lose spatial orientation.
• The Fix: Add a screen_direction field to your Shot List JSON (e.g., "Hero: screen-left facing right | Villain: screen-right facing left").
• Prompt Injection: Append that exact spatial lock to every Qwen-Image 2 storyboard prompt within the same scene so Mr. Potato Head stays on the left and Dark Broccoli stays on the right until you intentionally write a "camera crosses the line" transition shot.

2. Separate "Hero Close-Ups" from "Insert Cutaways"

AI video struggles most when two full bodies interact in a wide shot. Hollywood editors hide choreography limits by cutting rapidly between three distinct shot types:
• Wide / Master Shot (10% of fight): Establishes where they are in the room. Keep action minimal here (e.g., circling each other, sabers humming).
• Medium Action Shot (50% of fight): Waist-up swings and blocks. Because legs and footwork are out of frame, Wan 2.2 spends 100% of its spatial resolution on upper-body physics and facial expression.
• Macro Insert Shots (40% of fight): 0.8-to-1.2-second extreme close-ups with no faces—just hands gripping a hilt, sparks erupting off a countertop, or feet sliding across tiles. These render fast, never suffer from character drift, and make an action sequence feel twice as intense when stitched in FFmpeg.

3. Build "Environment Plates" Alongside Character Sheets

Character sheets keep your actors consistent, but Environment Plates keep your lighting and background props from morphing every time the camera cuts to a new angle.
• Pre-Render Reverse Angles: Before generating your shot list, have Qwen-Image 2 generate 3–4 static "empty set" images of your location from different angles (North wall, South wall, Low angle floor, Over-the-counter).
• Feed Both to Qwen-Image 2: For every storyboard frame, pass Character Reference Sheet + Angle-Specific Environment Plate into Qwen-Image 2. Now the lighting, shadows, and background clutter match across cuts automatically.

4. Use "Speed Ramping" and Audio Overlap in FFmpeg

Even great 1.5-second AI clips can feel slightly robotic if every cut plays at a uniform 1.0x speed with hard audio boundaries:
• Micro Speed-Ramps: In FFmpeg, slightly accelerate the wind-up of a swing (setpts=0.8*PTS) and slightly slow down the impact frame (setpts=1.25*PTS) using optical flow/RIFE. That subtle rhythm shift gives hits physical weight.
• Pre-Lap Sound Effects (J-Cuts): Have your pipeline trigger the sound of a saber ignition or clash 4 to 6 frames before the visual cut happens in FFmpeg. When the viewer hears the impact a split-second before seeing the close-up, the brain perceives seamless, continuous motion across two completely separate AI generations.
If you're ready to wire this into your codebase, I can help you design:
The two-tier JSON schema (Storyboard -> Granular Shot List) for your LLMAn FFmpeg filtergraph snippet for J-cut audio overlaps and micro speed-ramping

Sounds like this is a set of skills and mcp capabilities that I need to build/refine

--- further conversation ----
Mapping your AI filmmaking pipeline into Model Context Protocol (MCP) servers and Skills (declarative markdown workflows/system instructions that guide your agent) works cleanly when you separate deterministic execution (MCP tools) from creative direction and pacing rules (Skills).
Instead of one bloated MCP server, split the tools across three focused MCP servers by execution runtime and state, paired with four specialized Skills.

1. The MCP Server Architecture (Tools)


Server A: project-bible-mcp (State, Continuity & Asset Registry)

Backed by SQLite or structured JSON files on disk. Keeps your LLM from hallucinating asset paths or forgetting continuity rules across dozens of shots.
• upsert_character_asset(character_id, name, physical_traits, ref_sheet_path) — Registers a character and locks their canonical reference sheet.
• upsert_environment_plate(env_id, angle_name, lighting_notes, plate_image_path) — Registers empty background plates by camera angle.
• save_storyboard_beat(beat_id, scene_id, narrative_summary, screen_direction_lock, characters_present) — Stores high-level narrative beats and enforces the 180-degree spatial rule.
• save_micro_shot(shot_id, beat_id, order_index, shot_type, duration_frames, keyframe_prompt, motion_prompt, start_frame_path, end_frame_path, video_clip_path) — Stores the granular 1–2s shot plan and tracks rendering status (pending, drafted, rendered).
• get_scene_continuity_context(scene_id) — Returns active character sheets, environment plates, and the last rendered frame of the previous shot so the next shot chains cleanly.

Server B: comfyui-studio-mcp (Local 24GB VRAM GPU Execution)

Wraps the ComfyUI WebSocket/REST API (localhost:8188), loading parameterized workflow JSON templates and blocking until generation finishes.
• generate_reference_sheet(prompt, output_path, seed) — Runs Qwen-Image 2 to generate 3-angle character turnaround sheets or environment plates.
• render_storyboard_keyframe(char_ref_paths[], env_plate_path, composition_prompt, output_path, color_match_ref_path) — Runs Qwen-Image 2 multi-reference compositing + ColorMatch node to produce a locked static keyframe.
• extract_last_frame(video_path, output_image_path) — Grabs the cleanest final frame of a clip for frame-chaining (Shot A -> Shot B).
• render_draft_clip(start_image_path, motion_prompt, frames, output_path) — Runs LTX 2.5 at low resolution (512x288) for rapid timing validation.
• render_hero_clip(start_image_path, end_image_path, motion_prompt, frames, apply_rife_slowmo, output_path) — Runs Wan 2.2 (14B) with First-Last-Frame conditioning (when end_image_path is provided) and optional RIFE interpolation.

Server C: ffmpeg-post-mcp (Editorial Assembly & Audio)

Wraps local ffmpeg and ffprobe subprocesses for frame-accurate assembly.
• trim_and_normalize_clip(input_path, trim_head_frames, trim_tail_frames, speed_factor, output_path) — Strips AI ramp-up frames and applies micro speed-ramps.
• mix_audio_timeline(dialogue_tracks[], sfx_tracks[], music_track, output_wav_path) — Builds the master audio bed with J-cut pre-laps (triggering SFX 4–6 frames before visual cuts).
• assemble_final_cut(ordered_clip_paths[], master_audio_path, lut_cube_path, grain_intensity, output_mp4_path) — Concatenates trimmed clips, marries the audio, and bakes a unified 3D LUT + 35mm grain pass.

2. The Skills Layer (Agent Workflows & Guardrails)

Skills act as the "department heads" that instruct your LLM how and when to call the MCP tools above:

Skill Name	Purpose & Trigger	Key Rules Enforced in the Skill Prompt
lookdev-art-director	Triggered at project start or when new characters/sets appear.	• Must generate and register Character Turnaround Sheets and Environment Plates before any storyboard frames are drawn.
• Enforces neutral lighting on reference sheets.
action-cinematographer	Triggered when breaking a Storyboard Beat into a granular Shot List.	• Action Granularity Rule: Any fight/action beat must split into 1.0–2.0s (25–49 frames) micro-shots.
• 180-Degree Rule: Locks screen_direction across all shots in a beat.
• Shot Mix Rule: Enforces ~10% wide, ~50% medium, ~40% macro insert cutaways (hands, sparks, feet).
prompt-splitter	Triggered right before calling comfyui-studio-mcp.	• Zero Motion in Keyframes: Strips action verbs from keyframe_prompt (for Qwen-Image 2).
• Zero Appearance in Motion: Strips character/wardrobe adjectives from motion_prompt (for Wan 2.2), leaving only camera moves and physics.
editorial-finisher	Triggered after all shots in a scene are rendered.	• Trims first/last ~8–12 dead frames on non-chained clips.
• Offsets impact SFX by -5 frames (J-cut) before visual cuts.
• Applies a single LUT + grain pass across the whole sequence.


3. End-to-End Execution Loop (How They Work Together)

1. Pre-Production: The agent loads lookdev-art-director, calls comfyui-studio-mcp:generate_reference_sheet, and saves paths via project-bible-mcp.
2. Blocking: The agent loads action-cinematographer + prompt-splitter, expands a "lightsaber duel" storyboard beat into six 33-to-49-frame micro-shots, and writes them to project-bible-mcp.
3. Keyframing: For each shot, the agent fetches reference paths via get_scene_continuity_context, renders the static keyframe in Qwen-Image 2, and chains frames where needed.
4. Draft -> Hero: The agent batch-renders LTX 2.5 drafts first; once approved, it swaps the tool call to render_hero_clip (Wan 2.2 14B) using the exact same keyframes and motion prompts.
5. Post: The agent loads editorial-finisher and runs ffmpeg-post-mcp to trim, apply J-cut SFX, and grade the final MP4.
If you want to start building this out, I can provide:
• A starter FastMCP Python skeleton for one of the servers (e.g., comfyui-studio-mcp or project-bible-mcp)
• The full Markdown skill template for action-cinematographer and prompt-splitter
Let me know which piece you'd like to scaffold first!

---- keep asking questions ---
Beyond the core MCP architecture and shot decomposition, there are four practical realities that make or break an automated local AI filmmaking pipeline on a 24GB GPU:

1. VRAM Thrashing & Batch-by-Model Execution

Switching between three heavy models—Qwen-Image 2 (stills), LTX 2.5 (drafts), and Wan 2.2 14B (hero)—on a single 24GB RTX 4000 will waste minutes per shot unloading and reloading multi-gigabyte weights if your agent runs shot-by-shot sequentially.
• Group by Phase, Not by Shot: Design your agent loop to run all Qwen-Image 2 keyframes for a scene first, then unload/free VRAM (POST /free in ComfyUI's API), then batch-run all LTX 2.5 drafts, and finally batch-run all Wan 2.2 hero clips.
• Expose a unload_models() Tool: Add a VRAM flush tool to comfyui-studio-mcp so your agent explicitly clears model memory between pipeline stages and avoids Out-Of-Memory (OOM) crashes mid-batch.

2. Automated Quality Gate (Vision-Language Critic Loop)

Because local diffusion models still produce occasional extra limbs, backwards lightsaber hilts, or dead static frames, running an unattended batch without verification means you won't catch bad clips until FFmpeg stitches them.
• Add a inspect_render Tool: Have your pipeline extract the first, middle, and last frames of a generated clip and pass them to a multimodal/vision model (or a lightweight local VLM) alongside the original shot requirement.
• Auto-Retry Budget: Give your agent a strict rule in its skill: "If a keyframe or clip fails spatial/anatomy checks (e.g., character flipped sides, missing weapon, zero motion), increment the seed and retry up to 3 times before flagging the shot for human review."

3. Deterministic Seed & Asset Versioning

When you inevitably tweak one bad 1.5-second fight cut in a 30-shot sequence, you don't want to re-render the whole scene or lose track of which take was best.
• Never Overwrite Outputs: Have project-bible-mcp store an array of takes per shot_id (e.g., shot_04_take_01.mp4, shot_04_take_02.mp4) along with the exact seed, workflow JSON hash, and prompt used, plus an active_take_id pointer.
• Instant Swaps: Fixing a scene becomes as simple as telling your agent: "Re-roll Shot 04 with a stronger upward camera tilt and re-run assemble_final_cut."

4. Audio-Driven Timing (Generate Voice Before Hero Video)

Right now, you generate audio after all clips are done. That works great for pure action/SFX, but for any shot with dialogue, generating video first locks you into arbitrary clip lengths that rarely match natural speech cadence.
• Flip the Order for Dialogue Shots: For talking shots, generate the TTS/voice line first, measure its exact duration in seconds via ffprobe, and pass ceil(duration * 24) directly into duration_frames for Wan 2.2—followed by a local lip-sync pass (LatentSync or MuseTalk) before final FFmpeg assembly.
If you'd like to dive into implementation, let me know which area you want to tackle first:
FastMCP server boilerplate for comfyui-studio-mcp (including queue polling and /free VRAM management)SQLite schema & tools for project-bible-mcp with multi-take versioningSkill markdown files (action-cinematographer and editorial-finisher)

---- continued discussion ---
The biggest architectural mistake in local AI filmmaking is treating every shot with the same compute budget. On a 24GB RTX 4000, the secret to both fast iteration and cinema-grade final quality is building a 4-Tier Progressive Refinement Pipeline ("Animatic → Draft Video → Hero Video → Upscale/Polish") where you catch 90% of pacing, framing, and physics errors before your heavy Wan 2.2 14B model ever touches a frame.
Here is how to architect your draft vs. production tiers so you get rapid feedback without sacrificing final realism:

1. The 4-Tier Progressive Compute Architecture


Tier	Purpose	Local Engine / Model	Settings & Speed Tricks	When You Move to Next Tier
Tier 0: Static Animatic	Validate pacing, 180° screen direction, and dialogue timing in < 30 seconds.	Qwen-Image 2 (with 4-step Lightning/Turbo LoRA) + FFmpeg	Render keyframes at 768x432 (4 steps). Have FFmpeg stitch the static PNGs with Ken Burns pans/zooms and your rough TTS audio track.	Once the cut plays like a compelling comic-book/storyboard movie.
Tier 1: Motion Blocking (Draft)	Validate camera movement and fight physics in 3–6 seconds per clip.	LTX 2.5 (or distilled Wan 2.2 5B)	Resolution: 512x288 or 640x360
Steps: 6–8 steps
Sampler: euler / lcm	Once every 1.5s micro-shot has clean physics and no weird limb/saber morphing.
Tier 2: Hero Generation (Prod)	Render photorealistic textures, lighting, and facial micro-expressions.	Wan 2.2 (14B) (FP8 or Q5 GGUF)	Resolution: 832x480 or 960x544
Steps: 20–25 + TeaCache + SageAttention	Once individual clips look great natively.
Tier 3: Spatial & Temporal Polish	Unify resolution, smooth motion, and glue clips together.	SeedVR2 / Real-ESRGAN + RIFE + FFmpeg	Upscale 2x to 1080p, interpolate 24fps→48fps (or subtle slow-mo), apply unified 35mm grain + LUT.	Final export.


2. "Cheap" Tricks for Ultra-Fast Drafts (Tier 0 & Tier 1)

You shouldn't be waiting minutes per shot just to find out a camera angle feels awkward:
• Build a "Talking/Moving Animatic" First (Zero Video Diffusion): Before running any video model, stitch your Qwen-Image 2 storyboard frames directly in FFmpeg using zoompan filters (slow push-ins for tension, rapid whip-shake for impacts) synced to your audio. You can watch an entire 2-minute scene in 20 seconds and delete/rewrite bad shots immediately.
• Use Step-Distilled LoRAs for Drafts: Keep a 4-step or 8-step distilled LoRA (like CausVid / AccVideo / Turbo LoRAs) loaded in your draft ComfyUI workflow. Dropping from 25 steps to 6 steps at 512x288 gives you a 6x–8x speedup while preserving the exact same motion trajectory as the full-step run.
• Draft Only the "High-Risk" Action Shots: Establishing shots and slow dialogue pans rarely fail. In your Shot List JSON, flag shots with "risk_level": "high" (fight impacts, lunges, two-character interactions) and only run Tier 1 draft passes on those shots—send low-risk shots straight from Tier 0 keyframe to Tier 2 hero render.

3. Production Speed & Quality Boosters (Tier 2 & Tier 3)

When you are ready to run your final Wan 2.2 (14B) batch overnight or during a break, three architectural choices maximize quality on 24GB VRAM:
• Enable TeaCache + SageAttention (Free 1.6x–2x Speedup): If you aren't already using them in ComfyUI, SageAttention accelerates attention math on Ada/Blackwell/Ampere Tensor Cores with zero quality loss, and TeaCache caches redundant diffusion timesteps—cutting a 90-second Wan 2.2 render down to ~45 seconds with virtually identical visual fidelity.
• Render Native 480p/540p + Upscale, Don't Render Native 1080p: Never force Wan 2.2 14B to diffuse directly at 1080p on 24GB VRAM. Diffusion models have worse global coherence at ultra-high native resolutions and take 4x longer. Render at native 832x480 (or 960x544), then run a fast deterministic upscale pass (4x-UltraSharp or SeedVR2) at the end.
• Use TeaCache / First-Last-Frame Selectively: Only load the second image encoder (FLF2V) on shots that need exact end-pose landing (like a lightsaber block). Standard I2V (start-frame only) uses less VRAM and runs ~20% faster for everything else.

4. How Your Orchestrator Should Manage the Switch

In your pipeline config/database, store a single global or per-scene flag: render_tier = "animatic" | "draft" | "hero".
• animatic mode: Runs Qwen-Image 2 (fast 4-step) → FFmpeg zoompan + audio.
• draft mode: Reuses the exact same seed and storyboard_frame.png, swaps the ComfyUI template to ltx25_draft.json (512x288, 8 steps), and stitches in FFmpeg.
• hero mode: Re-renders the exact same seed and storyboard_frame.png through wan22_hero.json (832x480, 24 steps + TeaCache) → 2x upscale → FFmpeg final grade.
Because the keyframe image and seed stay locked across tiers, moving from a rough 5-second draft to a final production render requires zero re-prompting.
If you want to refine this architecture further, let me know:
• Whether you want the FFmpeg zoompan animatic command to preview static storyboards against audio before running video diffusion
• How you currently structure your shot-plan JSON so we can add risk_level and render_tier routing to it

----- continued discussion ----
To preview static storyboards against audio before running video diffusion, generate an animatic using FFmpeg. Pair your static Qwen-Image 2 keyframes with subtle simulated camera motion (zoompan), exact shot durations from your shot list, and your dialogue/SFX track so you can verify pacing and screen direction in seconds.

1. Define a Simple Shot Manifest (animatic.json)

Have your pipeline output a lightweight list mapping each storyboard image to its target duration (in seconds) and a simple camera move (push_in, pull_out, pan_left, or shake):
json
[
  {"image": "shots/shot_01.png", "duration": 2.0, "move": "push_in"},
  {"image": "shots/shot_02.png", "duration": 1.5, "move": "pan_left"},
  {"image": "shots/shot_03.png", "duration": 1.0, "move": "shake"}
]
Use code with caution.

2. Build the Animatic via FFmpeg (build_animatic.py)

FFmpeg's zoompan filter turns a single PNG into a 24fps video segment with smooth motion. By pre-scaling the image (scale=3840:-1) before zoompan, you avoid the jittery pixel-stepping common in default FFmpeg zooms.
python
import json
import subprocess
from pathlib import Path

FPS = 24
OUT_W, OUT_H = 1280, 720

MOVE_FILTERS = {
    # Slow push-in (1.0x -> 1.12x)
    "push_in": f"zoompan=z='min(zoom+0.0015,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={{frames}}:s={OUT_W}x{OUT_H}:fps={FPS}",
    # Slow pull-out (1.12x -> 1.0x)
    "pull_out": f"zoompan=z='if(lte(zoom,1.0),1.12,max(1.001,zoom-0.0015))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={{frames}}:s={OUT_W}x{OUT_H}:fps={FPS}",
    # Pan left-to-right at 1.1x zoom
    "pan_left": f"zoompan=z='1.1':x='(iw-iw/zoom)*(on/{{frames}})':y='ih/2-(ih/zoom/2)':d={{frames}}:s={OUT_W}x{OUT_H}:fps={FPS}",
    # High-frequency impact shake for fight beats
    "shake": f"zoompan=z='1.08':x='iw/2-(iw/zoom/2)+sin(on*2.5)*18':y='ih/2-(ih/zoom/2)+cos(on*3.1)*18':d={{frames}}:s={OUT_W}x{OUT_H}:fps={FPS}",
    # Static hold
    "static": f"zoompan=z='1.0':x='0':y='0':d={{frames}}:s={OUT_W}x{OUT_H}:fps={FPS}",
}

def render_animatic(manifest_path: str, audio_path: str, output_mp4: str):
    shots = json.loads(Path(manifest_path).read_text())
    temp_clips = []

    for idx, shot in enumerate(shots):
        frames = int(round(shot["duration"] * FPS))
        move_expr = MOVE_FILTERS[shot.get("move", "static")].format(frames=frames)
        # Optional: burn shot number and duration onto the corner for easy review
        drawtext = f"drawtext=text='Shot {idx+1} ({shot[\"duration\"]}s)':x=24:y=24:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6"
        vf = f"scale=3840:-1,{move_expr},{drawtext},format=yuv420p"

        clip_path = f"/tmp/animatic_{idx:03d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", shot["image"],
            "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            clip_path
        ], check=True)
        temp_clips.append(clip_path)

    # Write concat list
    concat_file = Path("/tmp/animatic_concat.txt")
    concat_file.write_text("\n".join(f"file '{c}'" for c in temp_clips))

    # Stitch clips and mux with your dialogue/SFX audio track
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        output_mp4
    ], check=True)
Use code with caution.

3. What to Check When Watching Your Animatic

• Dialogue Breathing Room: If a character's TTS line feels clipped or rushed against the cut, adjust duration in animatic.json before burning GPU time in Wan 2.2.
• Action Rhythm: For fast sequences (like your lightsaber duel), check if 1.0s–1.5s cuts feel punchy enough against the clash SFX, or if you need an extra 0.8s macro insert cutaway (hands, sparks) to bridge two poses.
• 180-Degree Continuity: Because shot numbers are burned into the top-left corner, you can immediately note "Shot 04 flipped Dark Broccoli to the left" and fix the Qwen-Image 2 keyframe before moving to Tier 1 video drafts.
If you want to integrate this directly into your setup, let me know:
• Whether you want to wrap this into your ffmpeg-post-mcp server as a build_animatic tool
• Or if you need help generating the aligned dialogue/SFX audio track from your Gemini 31B script first

----- continued discussion -----
To handle dynamic pacing (short action bursts vs. longer dramatic takes) alongside consistent voices and facial expressions on your local RTX 4000, your JSON schema should act as a routing contract that links Audio Timing, Visual Continuity, and Compute Tiering.

1. The Master Shot-Plan JSON Schema

Structure your JSON hierarchically: Scene -> Storyboard Beat -> Micro-Shot. Notice how shot_category, risk_level, and audio directly govern frame counts, draft routing, and lip-sync passes.
json
{
  "scene_id": "scene_03_kitchen_duel",
  "screen_direction_lock": "Hero (Mr. Potato Head): screen-left facing right | Villain (Dark Broccoli): screen-right facing left",
  "environment_plate_id": "env_kitchen_night_low_angle",
  "color_grade_lut": "luts/cinematic_teal_amber.cube",
  "storyboard_beats": [
    {
      "beat_id": "beat_01_taunt",
      "narrative_goal": "Dark Broccoli delivers a menacing ultimatum before igniting his saber.",
      "micro_shots": [
        {
          "shot_id": "s03_b01_sh01",
          "shot_category": "dialogue_drama",
          "risk_level": "low",
          "shot_type": "medium_close_up",
          "character_refs": ["char_dark_broccoli_sheet"],
          "expression_anchor": "narrowed eyes, tense jaw, menacing scowl, harsh under-lighting",
          "audio": {
            "voice_id": "dark_broccoli_baritone_v1",
            "dialogue_text": "You cannot mash what has already been steamed.",
            "tts_audio_path": "audio/s03_b01_sh01.wav",
            "duration_sec": 3.4,
            "lip_sync_required": true
          },
          "duration_frames": 85,
          "chain_from_prev_shot": false,
          "use_end_frame_anchor": false,
          "keyframe_prompt": "Medium close-up of Dark Broccoli on screen-right facing left, narrowed eyes, tense menacing scowl, 85mm anamorphic lens, moody kitchen counter background, chiaroscuro rim light.",
          "motion_prompt": "Slow subtle push-in, slight head tilt forward as he speaks, natural blinking, shallow depth of field.",
          "animatic_move": "push_in"
        }
      ]
    },
    {
      "beat_id": "beat_02_clash",
      "narrative_goal": "Rapid saber strike and block.",
      "micro_shots": [
        {
          "shot_id": "s03_b02_sh01",
          "shot_category": "action_impact",
          "risk_level": "high",
          "shot_type": "medium_action",
          "character_refs": ["char_mr_potato_sheet", "char_dark_broccoli_sheet"],
          "expression_anchor": "gritted teeth, wide focused eyes",
          "audio": {
            "sfx_cue": "sfx/saber_swing_clash.wav",
            "pre_lap_frames": -5
          },
          "duration_frames": 37,
          "chain_from_prev_shot": false,
          "use_end_frame_anchor": true,
          "keyframe_prompt": "Medium shot, Mr. Potato Head on screen-left lunging forward with glowing blue lightsaber raised, Dark Broccoli on screen-right raising red lightsaber to parry, dark kitchen, 35mm lens.",
          "end_keyframe_prompt": "Medium shot, blue and red lightsaber blades locked in an X clash center-frame, bright sparks illuminating both faces, intense strain.",
          "motion_prompt": "Fast explosive lunge forward, blades slamming together violently, camera handheld shake on impact, sparks flying.",
          "animatic_move": "shake"
        },
        {
          "shot_id": "s03_b02_sh02",
          "shot_category": "macro_insert",
          "risk_level": "low",
          "shot_type": "extreme_close_up",
          "character_refs": ["char_mr_potato_sheet"],
          "audio": {
            "sfx_cue": "sfx/plastic_foot_slide_tile.wav",
            "pre_lap_frames": -3
          },
          "duration_frames": 25,
          "chain_from_prev_shot": false,
          "use_end_frame_anchor": false,
          "keyframe_prompt": "Extreme macro close-up of a brown plastic shoe sliding backward across wet ceramic kitchen tile, blue saber reflection on floor, 100mm macro lens.",
          "motion_prompt": "Rapid backward skid, camera tracking low along the floor, dust particles kicking up.",
          "animatic_move": "pan_left"
        }
      ]
    }
  ]
}
Use code with caution.

2. How Your Orchestrator Routes Risk & Duration

Instead of letting the LLM guess frame counts, enforce deterministic routing rules in your orchestrator code based on shot_category and risk_level:
• Action Shots (shot_category: "action_impact", risk_level: "high"):
	• Frame Cap: Hard-capped at 25–49 frames (1.0s–2.0s).
	• Compute Path: Runs Tier 1 Draft (LTX 2.5 at 8 steps) first so you can verify physics in seconds. Automatically enables use_end_frame_anchor: true (Wan 2.2 First-Last-Frame conditioning) so the swing starts and lands in exact poses.
• Macro Cutaways (shot_category: "macro_insert", risk_level: "low"):
	• Frame Cap: 25–33 frames (1.0s–1.3s).
	• Compute Path: Skips Tier 1 draft and goes straight to Tier 2 Hero (Wan 2.2 I2V) because close-ups of hands, feet, or sparks rarely hallucinate anatomy.
• Dramatic Dialogue (shot_category: "dialogue_drama", risk_level: "low"):
	• Audio-First Frame Calculation: Your orchestrator generates the TTS audio before queuing ComfyUI, measures duration_sec via ffprobe, and sets duration_frames = ceil((duration_sec + 0.35) * 24) (typically 73–121 frames / 3–5s).
	• Compute Path: Skips Tier 1 draft, uses subtle camera motion (push_in or static), and routes through a post-render lip-sync node.

3. Keeping Voices & Facial Expressions Consistent Across Shots

AI characters feel fake when their voice pitch shifts between lines or their face snaps back to a blank "default mannequin stare" every time the camera cuts. Solve this with a 3-step Facial & Voice Lock:
1. Generate an "Expression Sheet" Alongside Your Turnaround Sheet:
When Qwen-Image 2 builds a character's master reference, generate a 2x2 Emotion Grid (neutral, angry/gritting teeth, shocked/fearful, smirking) on the same sheet. When your LLM writes expression_anchor for a shot, Qwen-Image 2 pulls the matching micro-expression directly from the reference sheet rather than inventing a new face shape.
2. Keep Wan 2.2 Mouths Closed During Dialogue Renders:
Do not ask Wan 2.2 or LTX 2.5 to "speak" or "move lips" in motion_prompt—diffusion video models produce rubbery, unconvincing mouth shapes when prompted to talk. Instead, prompt for emotional eye and head acting ("subtle brow furrow, slight nod, natural blinking, mouth closed at rest"), and let a dedicated lip-sync pass animate the lower face afterward.
3. Lock Voice Embeddings + Post-Render Lip-Sync (LatentSync / MuseTalk):
	• Voice Lock: Use a local voice engine that supports fixed speaker embeddings or reference audio cloning (such as Kokoro, F5-TTS, or Chatterbox) and save a permanent 5-second voice_ref.wav per character in project-bible-mcp. Every line for Dark Broccoli uses that exact seed/reference clip so timbre and cadence never drift.
	• Local Lip-Sync Node: For any shot where lip_sync_required: true, pass the finished Wan 2.2 clip + tts_audio_path through a LatentSync (or MuseTalk) node in ComfyUI. Because Wan 2.2 already rendered the lighting, skin texture, and upper-face eye emotion, LatentSync only modifies the jaw and lips to match the exact phonemes of your locked voice track.



