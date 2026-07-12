"""数据扩充层的黄金语料回归测试。

这是扩充层最重要的防线（MovieBot 最缺的东西）：语料覆盖真实站点的典型标题
形态与已知的踩坑用例。以后每次改词表/提取器，跑这份语料就知道有没有把
修好的 case 弄坏——新发现的坑修完必须往这里补用例。
"""

from __future__ import annotations

from movieclaw_enrich import ENRICH_VERSION, TorrentAttrs, enrich


class TestSceneMovies:
    """标准场景命名的电影标题。"""

    def test_typical_bluray_encode(self):
        a = enrich("Limbo.2021.1080p.BluRay.x265.10bit-WiKi")
        assert a.year == 2021
        assert a.resolution == "1080p"
        assert a.media_source == "Blu-ray"
        assert a.video_codec == "x265"
        assert a.release_group == "WiKi"
        assert a.remux is False
        assert a.seasons == [] and a.episodes == []

    def test_uhd_remux_with_dv_hdr10(self):
        a = enrich(
            "Oppenheimer.2023.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR10.TrueHD.7.1.Atmos-FGT"
        )
        assert a.year == 2023
        assert a.resolution == "2160p"
        assert a.media_source == "UHD Blu-ray"
        assert a.remux is True
        assert a.video_codec == "HEVC"
        assert set(a.hdr) == {"DV", "HDR10"}
        assert "TrueHD" in a.audio and "Atmos" in a.audio
        assert a.release_group == "FGT"

    def test_year_after_title_wins(self):
        # 场景惯例年份在片名后：片名里的 2001 不该盖过真实年份 1968
        a = enrich("2001.A.Space.Odyssey.1968.2160p.UHD.BluRay.x265-CHD")
        assert a.year == 1968
        assert a.release_group == "CHD"

    def test_year_range_takes_start(self):
        a = enrich("Tengen.Toppa.Gurren.Lagann.2007-2009.BluRay.1080p.MNHD-FRDS")
        assert a.year == 2007
        assert a.release_group == "FRDS"

    def test_dimension_notation(self):
        a = enrich("Some.Documentary.2020.3840x2160.WEB-DL.AAC.H264-NOGRP")
        assert a.resolution == "2160p"
        assert a.video_codec == "H.264"


class TestSceneTV:
    """剧集标题的季集提取。"""

    def test_single_episode(self):
        a = enrich("The.Last.of.Us.S02E03.2160p.WEB-DL.DDP5.1.Atmos.DV.HDR10-FLUX")
        assert a.seasons == [2]
        assert a.episodes == [3]
        assert a.year is None  # 标题里没有年份，不许猜
        assert a.resolution == "2160p"
        assert a.media_source == "WEB-DL"
        assert "DDP" in a.audio and "Atmos" in a.audio
        assert set(a.hdr) == {"DV", "HDR10"}
        assert a.release_group == "FLUX"

    def test_episode_range(self):
        a = enrich("Better.Call.Saul.S06E01-E07.1080p.WEB-DL.DDP5.1.H.264-NTb")
        assert a.seasons == [6]
        assert a.episodes == [1, 2, 3, 4, 5, 6, 7]
        assert a.release_group == "NTb"

    def test_season_pack_range(self):
        a = enrich("Fargo.S01-S05.COMPLETE.1080p.BluRay.x264-MIXED")
        assert a.seasons == [1, 2, 3, 4, 5]
        assert a.complete is True
        assert a.release_group == "Mixed"

    def test_bare_number_is_not_episode(self):
        # MovieBot 需要硬编码 'sense8' 补丁的经典坑：片名里的数字不是集号
        a = enrich("Sense8.S01.1080p.NF.WEB-DL.DD5.1.x264-NTb")
        assert a.seasons == [1]
        assert a.episodes == []


class TestChinesePT:
    """中文 PT 站点的标题/副标题形态。"""

    def test_subtitle_fills_missing_episodes(self):
        # 主标题没有集数，副标题的「第19-20集」要补进来
        a = enrich(
            "Dragon.City.2023.1080p.WEB-DL.H264.AAC-HHWEB",
            "龙城 第19-20集 | 类型:剧情/家庭 | 主演:马伊琍/白宇/刘琳",
        )
        assert a.episodes == [19, 20]
        assert a.year == 2023
        assert a.release_group == "HHWEB"  # 未知组原样保留

    def test_title_takes_priority_over_subtitle(self):
        # 双源冲突时主标题优先：副标题的 720p 不该盖过主标题的 1080p
        a = enrich(
            "Show.S01E05.1080p.WEB-DL.AAC.H264-CHDWEB",
            "综艺 720p 第5期",
        )
        assert a.resolution == "1080p"
        assert a.episodes == [5]

    def test_chinese_numeral_season_and_complete(self):
        a = enrich(
            "Quanzhi.Fashi.S05.2023.1080p.WEB-DL.H265.AAC-CMCT",
            "全职法师 第五季 全12集 | 国语中字",
        )
        assert a.seasons == [5]
        assert a.episodes == list(range(1, 13))
        assert a.complete is True
        assert a.release_group == "CMCT"

    def test_year_in_cjk_title_not_extracted(self):
        # 《请回答1988》：紧贴中文的数字是片名的一部分，不是年份
        a = enrich("请回答1988 第01-20集 1080p WEB-DL H264 AAC")
        assert a.year is None
        assert a.episodes == list(range(1, 21))

    def test_variety_show_issue_number_not_year(self):
        # 综艺的「第2024期」既不是年份也不该当成集号
        a = enrich("大侦探 第2024期 4K WEB-DL", "芒果TV 全网首播")
        assert a.year is None
        assert a.episodes == []
        assert a.resolution == "2160p"

    def test_trailing_bracket_decoration(self):
        a = enrich("Wonderland.2024.1080p.WEB-DL.H264.DDP5.1-CHDBits[国语中字]")
        assert a.release_group == "CHD"  # 词表归一 CHDBits → CHD
        assert "DDP" in a.audio


class TestAudioAndHDR:
    """音频与 HDR 的掩蔽/归一细节。"""

    def test_dts_hd_ma_masks_shorter_keys(self):
        # 'DTS-HD MA' 命中后，'DTS-HD' 和 'DTS' 不得在其内部二次命中
        a = enrich("Movie.2020.1080p.BluRay.DTS-HD.MA.5.1.x264-GROUP")
        assert a.audio == ["DTS-HD MA"]

    def test_multi_audio_marker(self):
        a = enrich("Movie.2019.BluRay.1080p.x265.10bit.2Audio.MNHD-FRDS")
        assert "2Audio" in a.audio

    def test_hdr_not_matched_inside_hdrip(self):
        # 'HDR' 不得命中 'HDRip' 内部（词边界守卫）
        a = enrich("Old.Movie.2005.HDRip.x264-TEAM")
        assert a.hdr == []
        assert a.media_source == "HDRip"

    def test_web_not_matched_inside_webrip(self):
        a = enrich("Show.S01.720p.WEBRip.AAC.x264-GRP")
        assert a.media_source == "WEBRip"


class TestReleaseGroupGuards:
    """压制组提取的反例守卫。"""

    def test_technical_tail_is_not_group(self):
        a = enrich("Movie.2023.2160p.UHD.BluRay.HEVC.DTS-HD.MA.5.1-REMUX")
        assert a.release_group is None

    def test_hyphen_tech_fragment_tail_is_not_group(self):
        # 标题以 WEB-DL / Blu-ray / DTS-HD 结尾时，'-DL'/'-ray'/'-HD' 残片不是组名
        assert enrich("Soul.Land.2.S01E96.4K.WEB-DL").release_group is None
        assert enrich("Movie.2020.1080p.Blu-ray").release_group is None
        assert enrich("Movie.2021.BluRay.DTS-HD").release_group is None

    def test_pure_digit_tail_is_not_group(self):
        a = enrich("Movie.2023.1080p.WEB-DL.H264-2023")
        assert a.release_group is None

    def test_no_group_returns_none(self):
        a = enrich("某部电影 2023 1080p 国语中字")
        assert a.release_group is None


class TestMediaType:
    """影视类型推断：站点分类先验 + 季集观测的联合判定。"""

    def test_movie_category_wins(self):
        # 站点标电影就是电影——即使标题带 COMPLETE（电影三部曲合集）
        a = enrich("The.Godfather.Trilogy.COMPLETE.1080p.BluRay.x264-GRP", category="movie")
        assert a.media_type == "movie"

    def test_tv_category_wins(self):
        a = enrich("Some.Show.2023.1080p.WEB-DL.H264-GRP", category="tv")
        assert a.media_type == "tv"

    def test_anime_with_episode_marker_is_tv(self):
        # PT 站的动漫分类混杂电影和剧集，靠季集观测判定（MovieBot 坑 6）
        a = enrich("One.Piece.E1071.1080p.WEB-DL.AAC-VARYG", category="anime")
        assert a.media_type == "tv"

    def test_anime_without_marker_model_recognizes_movie(self):
        # 动漫分类没有季集标记：旧规则只能返回 None（不猜）；模型能从命名
        # 形态判定是剧场版电影——这是 v3 换模型后的能力升级
        a = enrich("Suzume.2022.1080p.BluRay.x265-Ao", category="anime")
        assert a.media_type == "movie"

    def test_documentary_complete_pack_is_tv(self):
        a = enrich("Planet.Earth.III.2023.2160p", "行星地球3 全8集", category="documentary")
        assert a.media_type == "tv"

    def test_no_category_model_judges_from_text(self):
        assert enrich("Show.S02E05.1080p.WEB-DL-GRP").media_type == "tv"
        # 旧规则无季集标记时无法判定电影（None）；模型可以——v3 能力升级
        assert enrich("Movie.2023.1080p.BluRay.x264-GRP").media_type == "movie"

    def test_non_video_category_never_labeled(self):
        # 音乐合集也会写"全12期"，非影视分类下季集证据不可信
        a = enrich("某音乐现场 全12期 FLAC", category="music")
        assert a.media_type is None


class TestPipeline:
    """管线整体行为。"""

    def test_empty_input(self):
        a = enrich("")
        assert a == TorrentAttrs()

    def test_version_constant_is_int(self):
        assert isinstance(ENRICH_VERSION, int) and ENRICH_VERSION >= 1

    def test_broken_extractor_is_isolated(self):
        # 单个提取器抛异常只跳过自己，其它字段照常产出
        from movieclaw_enrich import extractors

        def _boom(_text: str) -> dict[str, object]:
            raise RuntimeError("boom")

        extractors_backup = list(extractors.EXTRACTORS)
        extractors.EXTRACTORS.insert(0, ("boom", _boom))
        try:
            a = enrich("Limbo.2021.1080p.BluRay.x265-WiKi")
            assert a.year == 2021
            assert a.resolution == "1080p"
        finally:
            extractors.EXTRACTORS[:] = extractors_backup

    def test_serialization_roundtrip(self):
        # 落库存 JSON、读回重建的往返必须无损
        a = enrich("Movie.2023.2160p.WEB-DL.DDP5.1.Atmos.DV.H265-CHDWEB")
        data = a.model_dump(mode="json", exclude_defaults=True)
        assert TorrentAttrs(**data) == a
