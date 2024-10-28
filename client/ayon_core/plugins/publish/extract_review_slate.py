import os
import re
import subprocess
from pprint import pformat

import pyblish.api

from ayon_core.lib import (
    path_to_subprocess_arg,
    run_subprocess,
    get_ffmpeg_tool_args,
    get_ffprobe_data,
    get_ffprobe_streams,
    get_ffmpeg_codec_args,
    get_ffmpeg_format_args,
)
from ayon_core.pipeline import publish
from ayon_core.pipeline.publish import KnownPublishError


class ExtractReviewSlate(publish.Extractor):
    """
    Will add slate frame at the start of the video files
    """

    label = "Review with Slate frame"
    order = pyblish.api.ExtractorOrder + 0.031
    families = ["slate", "review"]
    match = pyblish.api.Subset

    SUFFIX = "_slate"

    hosts = ["nuke", "shell"]
    optional = True

    def process(self, instance):
        inst_data = instance.data
        if "representations" not in inst_data:
            raise RuntimeError("Burnin needs already created mov to work on.")

        # get slates frame from upstream
        slates_data = inst_data.get("slateFrames")
        if not slates_data:
            # make it backward compatible and open for slates generator
            # premium plugin
            slates_data = {
                "*": inst_data["slateFrame"]
            }

        self.log.debug("_ slates_data: {}".format(pformat(slates_data)))

        if "reviewToWidth" in inst_data:
            use_legacy_code = True
        else:
            use_legacy_code = False

        pixel_aspect = inst_data.get("pixelAspect", 1)
        fps = inst_data.get("fps")
        self.log.debug("fps {} ".format(fps))

        for idx, repre in enumerate(inst_data["representations"]):
            self.log.debug("repre ({}): `{}`".format(idx + 1, repre))

            p_tags = repre.get("tags", [])
            if "slate-frame" not in p_tags:
                continue

            # get repre file
            stagingdir = repre["stagingDir"]
            input_file = "{0}".format(repre["files"])
            input_path = os.path.join(
                os.path.normpath(stagingdir), repre["files"])
            self.log.debug("__ input_path: {}".format(input_path))

            streams = get_ffprobe_streams(
                input_path, self.log
            )
            # get slate data
            slate_path = self._get_slate_path(input_file, slates_data)
            self.log.debug("_ slate_path: {}".format(slate_path))

            slate_width, slate_height = self._get_slates_resolution(slate_path)

            # Get video metadata
            (
                input_width,
                input_height,
                input_timecode,
                input_frame_rate,
                input_pixel_aspect
            ) = self._get_video_metadata(streams)
            if input_pixel_aspect:
                pixel_aspect = input_pixel_aspect

            # Raise exception of any stream didn't define input resolution
            if input_width is None:
                raise KnownPublishError(
                    "FFprobe couldn't read resolution from input file: \"{}\""
                    .format(input_path)
                )

            (
                audio_codec,
                audio_channels,
                audio_sample_rate,
                audio_channel_layout,
                input_audio
            ) = self._get_audio_metadata(streams)

            # values are set in ExtractReview
            if use_legacy_code:
                to_width = inst_data["reviewToWidth"]
                to_height = inst_data["reviewToHeight"]
            else:
                to_width = input_width
                to_height = input_height

            self.log.debug("to_width: `{}`".format(to_width))
            self.log.debug("to_height: `{}`".format(to_height))

            # defining image ratios
            resolution_ratio = (
                (float(slate_width) * pixel_aspect) / slate_height
            )
            delivery_ratio = float(to_width) / float(to_height)
            self.log.debug("resolution_ratio: `{}`".format(resolution_ratio))
            self.log.debug("delivery_ratio: `{}`".format(delivery_ratio))

            # get scale factor
            scale_factor_by_height = float(to_height) / slate_height
            scale_factor_by_width = float(to_width) / (
                slate_width * pixel_aspect
            )

            # shorten two decimals long float number for testing conditions
            resolution_ratio_test = float("{:0.2f}".format(resolution_ratio))
            delivery_ratio_test = float("{:0.2f}".format(delivery_ratio))

            self.log.debug("__ scale_factor_by_width: `{}`".format(
                scale_factor_by_width
            ))
            self.log.debug("__ scale_factor_by_height: `{}`".format(
                scale_factor_by_height
            ))

            _remove_at_end = []

            ext = os.path.splitext(input_file)[1]
            output_file = input_file.replace(ext, "") + self.SUFFIX + ext

            _remove_at_end.append(input_path)

            output_path = os.path.join(
                os.path.normpath(stagingdir), output_file)
            self.log.debug("__ output_path: {}".format(output_path))

            input_args = []
            output_args = []

            # preset's input data
            if use_legacy_code:
                input_args.extend(repre["_profile"].get('input', []))
            else:
                input_args.extend(repre["outputDef"].get('input', []))

            input_args.extend([
                "-loop", "1",
                "-i", path_to_subprocess_arg(slate_path),
                "-r", str(input_frame_rate),
                "-frames:v", "1",
            ])

            # add timecode from source to the slate, substract one frame
            offset_timecode = ""
            if input_timecode:
                offset_timecode = self._tc_offset(
                    str(input_timecode),
                    framerate=fps,
                    frame_offset=-1
                )
                self.log.debug("Slate Timecode: `{}`".format(
                    offset_timecode
                ))

            if use_legacy_code:
                format_args = []
                codec_args = repre["_profile"].get('codec', [])
                output_args.extend(codec_args)
                # preset's output data
                output_args.extend(repre["_profile"].get('output', []))
            else:
                # Codecs are copied from source for whole input
                format_args, codec_args = self._get_format_codec_args(repre)
                output_args.extend(format_args)
                output_args.extend(codec_args)

            # make sure colors are correct
            output_args.extend([
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                "-colorspace", "bt709",
            ])

            # scaling none square pixels and 1920 width
            if (
                # Always scale slate if not legacy
                not use_legacy_code or
                # Legacy code required reformat tag
                (use_legacy_code and "reformat" in p_tags)
            ):
                if resolution_ratio_test < delivery_ratio_test:
                    self.log.debug("lower then delivery")
                    width_scale = int(slate_width * scale_factor_by_height)
                    width_half_pad = int((to_width - width_scale) / 2)
                    height_scale = to_height
                    height_half_pad = 0
                else:
                    self.log.debug("heigher then delivery")
                    width_scale = to_width
                    width_half_pad = 0
                    height_scale = int(slate_height * scale_factor_by_width)
                    height_half_pad = int((to_height - height_scale) / 2)

                self.log.debug(
                    "__ width_scale: `{}`".format(width_scale)
                )
                self.log.debug(
                    "__ width_half_pad: `{}`".format(width_half_pad)
                )
                self.log.debug(
                    "__ height_scale: `{}`".format(height_scale)
                )
                self.log.debug(
                    "__ height_half_pad: `{}`".format(height_half_pad)
                )

                scaling_arg = (
                    "scale={0}x{1}:flags=lanczos"
                    ":out_color_matrix=bt709"
                    ",pad={2}:{3}:{4}:{5}:black"
                    ",setsar=1"
                    ",fps={6}"
                ).format(
                    width_scale,
                    height_scale,
                    to_width,
                    to_height,
                    width_half_pad,
                    height_half_pad,
                    input_frame_rate
                )

                vf_back = self.add_video_filter_args(output_args, scaling_arg)
                # add it to output_args
                output_args.insert(0, vf_back)

            # overrides output file
            output_args.append("-y")

            slate_v_path = slate_path.replace(".png", ext)
            output_args.append(
                path_to_subprocess_arg(slate_v_path)
            )
            _remove_at_end.append(slate_v_path)

            slate_args = [
                subprocess.list2cmdline(get_ffmpeg_tool_args("ffmpeg")),
                " ".join(input_args),
                " ".join(output_args)
            ]
            slate_subprocess_cmd = " ".join(slate_args)

            # run slate generation subprocess
            self.log.debug(
                "Slate Executing: {}".format(slate_subprocess_cmd)
            )
            run_subprocess(
                slate_subprocess_cmd, shell=True, logger=self.log
            )

            # Create slate with silent audio track
            if input_audio:
                # silent slate output path
                slate_silent_path = "_silent".join(
                    os.path.splitext(slate_v_path))
                _remove_at_end.append(slate_silent_path)
                self._create_silent_slate(
                    slate_v_path,
                    slate_silent_path,
                    audio_codec,
                    audio_channels,
                    audio_sample_rate,
                    audio_channel_layout,
                    input_frame_rate
                )

                # replace slate with silent slate for concat
                slate_v_path = slate_silent_path

            # concat slate and videos together with concat filter
            # this will reencode the output
            if input_audio:
                fmap = [
                    "-filter_complex",
                    "[0:v] [0:a] [1:v] [1:a] concat=n=2:v=1:a=1 [v] [a]",
                    "-map", '[v]',
                    "-map", '[a]'
                ]
            else:
                fmap = [
                    "-filter_complex",
                    "[0:v] [1:v] concat=n=2:v=1:a=0 [v]",
                    "-map", '[v]'
                ]
            concat_args = get_ffmpeg_tool_args(
                "ffmpeg",
                "-y",
                "-i", slate_v_path,
                "-i", input_path,
            )
            concat_args.extend(fmap)
            if offset_timecode:
                concat_args.extend(["-timecode", offset_timecode])
            # NOTE: Added because of OP Atom demuxers
            # Add format arguments if there are any
            # - keep format of output
            if format_args:
                concat_args.extend(format_args)

            if codec_args:
                concat_args.extend(codec_args)

            # Use arguments from ffmpeg preset
            source_ffmpeg_cmd = repre.get("ffmpeg_cmd")
            if source_ffmpeg_cmd:
                copy_args = (
                    "-metadata",
                    "-metadata:s:v:0",
                    "-b:v",
                    "-b:a",
                )
                args = source_ffmpeg_cmd.split(" ")
                for indx, arg in enumerate(args):
                    if arg in copy_args:
                        concat_args.append(arg)
                        # assumes arg has one parameter
                        concat_args.append(args[indx + 1])

            # add final output path
            concat_args.append(output_path)

            # ffmpeg concat subprocess
            self.log.debug(
                "Executing concat filter: {}".format
                (" ".join(concat_args))
            )
            run_subprocess(
                concat_args, logger=self.log
            )

            self.log.debug("__ repre[tags]: {}".format(repre["tags"]))
            repre_update = {
                "files": output_file,
                "name": repre["name"],
                "tags": [x for x in repre["tags"] if x != "delete"]
            }
            inst_data["representations"][idx].update(repre_update)
            self.log.debug(
                "_ representation {}: `{}`".format(
                    idx, inst_data["representations"][idx]))

            # removing temp files
            for f in _remove_at_end:
                os.remove(f)
                self.log.debug("Removed: `{}`".format(f))

        # Remove any representations tagged for deletion.
        for repre in inst_data.get("representations", []):
            tags = repre.get("tags", [])
            if "delete" not in tags:
                continue
            if "need_thumbnail" in tags:
                continue
            self.log.debug("Removing representation: {}".format(repre))
            inst_data["representations"].remove(repre)

        self.log.debug(inst_data["representations"])

    def _get_slate_path(self, input_file, slates_data):
        slate_path = None
        for sl_n, _slate_path in slates_data.items():
            if "*" in sl_n:
                slate_path = _slate_path
                break
            elif re.search(sl_n, input_file):
                slate_path = _slate_path
                break

        if not slate_path:
            raise AttributeError(
                "Missing slates paths: {}".format(slates_data))

        return slate_path

    def _get_slates_resolution(self, slate_path):
        slate_streams = get_ffprobe_streams(slate_path, self.log)
        # Try to find first stream with defined 'width' and 'height'
        # - this is to avoid order of streams where audio can be as first
        # - there may be a better way (checking `codec_type`?)+
        slate_width = None
        slate_height = None
        for slate_stream in slate_streams:
            if "width" in slate_stream and "height" in slate_stream:
                slate_width = int(slate_stream["width"])
                slate_height = int(slate_stream["height"])
                break

        # Raise exception of any stream didn't define input resolution
        if slate_width is None:
            raise AssertionError((
                "FFprobe couldn't read resolution from input file: \"{}\""
            ).format(slate_path))

        return (slate_width, slate_height)

    def _get_video_metadata(self, streams):
        input_timecode = ""
        input_width = None
        input_height = None
        input_frame_rate = None
        input_pixel_aspect = None
        for stream in streams:
            if stream.get("codec_type") != "video":
                continue
            self.log.debug("FFprobe Video: {}".format(stream))

            if "width" not in stream or "height" not in stream:
                continue
            width = int(stream["width"])
            height = int(stream["height"])
            if not width or not height:
                continue

            # Make sure that width and height are captured even if frame rate
            #    is not available
            input_width = width
            input_height = height

            input_pixel_aspect = stream.get("sample_aspect_ratio")
            if input_pixel_aspect is not None:
                try:
                    input_pixel_aspect = float(
                        eval(str(input_pixel_aspect).replace(':', '/')))
                except Exception:
                    self.log.debug(
                        "__Converting pixel aspect to float failed: {}".format(
                            input_pixel_aspect))

            tags = stream.get("tags") or {}
            input_timecode = tags.get("timecode") or ""

            input_frame_rate = stream.get("r_frame_rate")
            if input_frame_rate is not None:
                break
        return (
            input_width,
            input_height,
            input_timecode,
            input_frame_rate,
            input_pixel_aspect
        )

    def _get_audio_metadata(self, streams):
        # Get audio metadata
        audio_codec = None
        audio_channels = None
        audio_sample_rate = None
        audio_channel_layout = None
        input_audio = False

        for stream in streams:
            if stream.get("codec_type") != "audio":
                continue
            self.log.debug("__Ffprobe Audio: {}".format(stream))

            if all(
                stream.get(key)
                for key in (
                    "codec_name",
                    "channels",
                    "sample_rate",
                    "channel_layout",
                )
            ):
                audio_codec = stream["codec_name"]
                audio_channels = stream["channels"]
                audio_sample_rate = stream["sample_rate"]
                audio_channel_layout = stream["channel_layout"]
                input_audio = True
                break

        return (
            audio_codec,
            audio_channels,
            audio_sample_rate,
            audio_channel_layout,
            input_audio,
        )

    def _create_silent_slate(
        self,
        src_path,
        dst_path,
        audio_codec,
        audio_channels,
        audio_sample_rate,
        audio_channel_layout,
        input_frame_rate
    ):
        # Get duration of one frame in micro seconds
        items = input_frame_rate.split("/")
        if len(items) == 1:
            one_frame_duration = 1.0 / float(items[0])
        elif len(items) == 2:
            one_frame_duration = float(items[1]) / float(items[0])
        else:
            one_frame_duration = None

        if one_frame_duration is None:
            one_frame_duration = "40000us"
        else:
            one_frame_duration *= 1000000
            one_frame_duration = str(int(one_frame_duration)) + "us"
        self.log.debug("One frame duration is {}".format(one_frame_duration))

        slate_silent_args = get_ffmpeg_tool_args(
            "ffmpeg",
            "-i", src_path,
            "-f", "lavfi", "-i",
            "anullsrc=r={}:cl={}:d={}".format(
                audio_sample_rate,
                audio_channel_layout,
                one_frame_duration
            ),
            "-c:v", "copy",
            "-c:a", audio_codec,
            "-map", "0:v",
            "-map", "1:a",
            "-shortest",
            "-y",
            dst_path
        )
        # run slate generation subprocess
        self.log.debug("Silent Slate Executing: {}".format(
            " ".join(slate_silent_args)
        ))
        run_subprocess(
            slate_silent_args, logger=self.log
        )

    def add_video_filter_args(self, args, inserting_arg):
        """
        Fixing video filter argumets to be one long string

        Args:
            args (list): list of string arguments
            inserting_arg (str): string argument we want to add
                                 (without flag `-vf`)

        Returns:
            str: long joined argument to be added back to list of arguments

        """
        # find all video format settings
        vf_settings = [p for p in args
                       for v in ["-filter:v", "-vf"]
                       if v in p]
        self.log.debug("_ vf_settings: `{}`".format(vf_settings))

        # remove them from output args list
        for p in vf_settings:
            self.log.debug("_ remove p: `{}`".format(p))
            args.remove(p)
            self.log.debug("_ args: `{}`".format(args))

        # strip them from all flags
        vf_fixed = [p.replace("-vf ", "").replace("-filter:v ", "")
                    for p in vf_settings]

        self.log.debug("_ vf_fixed: `{}`".format(vf_fixed))
        vf_fixed.insert(0, inserting_arg)
        self.log.debug("_ vf_fixed: `{}`".format(vf_fixed))
        # create new video filter setting
        vf_back = "-vf " + ",".join(vf_fixed)

        return vf_back

    def _get_format_codec_args(self, repre):
        """Detect possible codec arguments from representation."""
        codec_args = []

        # Get one filename of representation files
        filename = repre["files"]
        # If files is list then pick first filename in list
        if isinstance(filename, (tuple, list)):
            filename = filename[0]
        # Get full path to the file
        full_input_path = os.path.join(repre["stagingDir"], filename)

        try:
            # Get information about input file via ffprobe tool
            ffprobe_data = get_ffprobe_data(full_input_path, self.log)
        except Exception:
            self.log.warning(
                "Could not get codec data from input.",
                exc_info=True
            )
            return codec_args

        source_ffmpeg_cmd = repre.get("ffmpeg_cmd")
        format_args = get_ffmpeg_format_args(ffprobe_data, source_ffmpeg_cmd)
        codec_args = get_ffmpeg_codec_args(
            ffprobe_data, source_ffmpeg_cmd, logger=self.log
        )

        return format_args, codec_args

    def _tc_offset(self, timecode, framerate=24.0, frame_offset=-1):
        """Offsets timecode by frame"""
        def _seconds(value, framerate):
            if isinstance(value, str):
                _zip_ft = zip((3600, 60, 1, 1 / framerate), value.split(':'))
                _s = sum(f * float(t) for f, t in _zip_ft)
            elif isinstance(value, (int, float)):
                _s = value / framerate
            else:
                _s = 0
            return _s

        def _frames(seconds, framerate, frame_offset):
            _f = seconds * framerate + frame_offset
            if _f < 0:
                _f = framerate * 60 * 60 * 24 + _f
            return _f

        def _timecode(seconds, framerate):
            return '{h:02d}:{m:02d}:{s:02d}:{f:02d}'.format(
                h=int(seconds / 3600),
                m=int(seconds / 60 % 60),
                s=int(seconds % 60),
                f=int(round((seconds - int(seconds)) * framerate)))
        drop = False
        if ';' in timecode:
            timecode = timecode.replace(';', ':')
            drop = True
        frames = _frames(
            _seconds(timecode, framerate),
            framerate,
            frame_offset
        )
        tc = _timecode(_seconds(frames, framerate), framerate)
        if drop:
            tc = ';'.join(tc.rsplit(':', 1))
        return tc
