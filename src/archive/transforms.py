from typing import Any, Optional
import re
import json
import xarray as xr
from pathlib import Path


class MapDict:

    def __init__(self, transform) -> None:
        self.transform = transform

    def __call__(self, datasets: dict[str, xr.Dataset]) -> dict[str, xr.Dataset]:

        out = {}
        for key, dataset in datasets.items():
            try:
                out[key] = self.transform(dataset)
            except Exception as e:
                raise RuntimeError(f"{key}: {e}")
        return out


class RenameDimensions:

    def __init__(self) -> None:
        with Path("mappings/dim_names.json").open("r") as handle:
            self.dimension_mapping = json.load(handle)

    def __call__(self, dataset: xr.Dataset) -> xr.Dataset:
        name = dataset.attrs["name"]
        dataset = dataset.squeeze()
        if name in self.dimension_mapping:
            dims = self.dimension_mapping[name]
            dataset = dataset.rename_dims(self.dimension_mapping[name])
            for old_name, new_name in dims.items():
                if old_name in dataset.coords:
                    dataset = dataset.rename_vars({old_name: new_name})
            dataset.attrs["dims"] = list(dataset.sizes.keys())
        return dataset


class DropZeroDimensions:

    def __call__(self, dataset: xr.Dataset) -> Any:
        for key, coord in dataset.coords.items():
            if (coord.values == 0).all():
                dataset = dataset.drop_vars(key)
        return dataset


class DropDatasets:

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def __call__(self, datasets: dict[str, xr.Dataset]) -> dict[str, xr.Dataset]:
        for key in self.keys:
            datasets.pop(key)
        return datasets


class StandardizeSignalDataset:

    def __init__(self, source: str) -> None:
        self.source = source

    def __call__(self, dataset: xr.Dataset) -> xr.Dataset:
        dataset = dataset.squeeze(drop=True)

        name = dataset.attrs["name"]
        # Drop error if all zeros
        if (dataset["error"].values == 0).all():
            dataset = dataset.drop_vars("error")

        # Rename variables
        new_names = {}
        if "error" in dataset:
            new_names["data"] = name
            new_names["error"] = "_".join([name, "error"])
        else:
            name = name + "_" if name == "time" else name
            new_names["data"] = name

        dataset = dataset.rename(new_names)

        # Update attributes
        attrs = dataset.attrs
        attrs["name"] = self.source + "/" + new_names["data"]
        dataset[new_names["data"]].attrs = attrs
        return dataset


class MergeDatasets:

    def __call__(self, dataset_dict: dict[str, xr.Dataset]) -> xr.Dataset:
        dataset = xr.merge(dataset_dict.values())
        dataset.attrs = {}
        return dataset


class TensorizeChannels:
    def __init__(
        self,
        stem: str,
        regex: Optional[str] = None,
        dim_name: Optional[str] = None,
        assign_coords: bool = True,
    ) -> None:
        self.stem = stem
        self.regex = regex if regex is not None else stem + "(\d+)"
        self.dim_name = f"{self.stem}_channel" if dim_name is None else dim_name
        self.assign_coords = assign_coords

    def __call__(self, dataset: xr.Dataset) -> xr.Dataset:

        group_keys = self._get_group_keys(dataset)
        channels = [dataset[key] for key in group_keys]
        dataset[self.stem] = xr.combine_nested(channels, concat_dim=self.dim_name)

        if self.assign_coords:
            dataset[self.stem] = dataset[self.stem].assign_coords(
                {self.dim_name: group_keys}
            )

        dataset[self.stem] = dataset[self.stem].chunk("auto")
        dataset = dataset.drop_vars(group_keys)
        return dataset

    def _get_group_keys(self, dataset: xr.Dataset) -> list[str]:
        group_keys = dataset.data_vars.keys()
        group_keys = [
            key for key in group_keys if re.search(self.regex, key) is not None
        ]
        group_keys = self._sort_numerically(group_keys)
        return group_keys

    def _parse_digits(self, s):
        # Split the string into a list of numeric and non-numeric parts
        parts = re.split(self.regex, s)
        # Convert numeric parts to integers
        return [int(part) if part.isdigit() else part for part in parts]

    def _sort_numerically(self, strings: list[str]) -> list[str]:
        return sorted(strings, key=self._parse_digits)


class ASXTransform:
    """ASX is very special.

    The time points are actually the data and the data is blank.
    This transformation renames them and used the correct dimension mappings.
    """

    def __init__(self) -> None:
        with Path("mappings/dim_names.json").open("r") as handle:
            self.dimension_mapping = json.load(handle)

    def __call__(self, dataset: xr.Dataset) -> xr.Dataset:
        dataset = dataset.squeeze()
        name = dataset.attrs["name"]

        if not name in self.dimension_mapping:
            return dataset

        dataset = dataset.rename_dims(self.dimension_mapping[name])
        dataset = dataset.drop("data")
        dataset["data"] = dataset["time"]
        dataset = dataset.drop("time")
        return dataset


class Pipeline:

    def __init__(self, transforms: list):
        self.transforms = transforms

    def __call__(self, x: Any) -> Any:
        for transform in self.transforms:
            x = transform(x)
        return x


class PipelineRegistry:

    def __init__(self) -> None:
        self.pipelines = {
            "abm": Pipeline(
                [
                    MapDict(RenameDimensions()),
                    MapDict(DropZeroDimensions()),
                    MapDict(StandardizeSignalDataset("abm")),
                    MergeDatasets(),
                ]
            ),
            "ada": Pipeline(
                [
                    MapDict(RenameDimensions()),
                    MapDict(StandardizeSignalDataset("ada")),
                    MergeDatasets(),
                ]
            ),
            "aga": Pipeline(
                [
                    MapDict(RenameDimensions()),
                    MapDict(StandardizeSignalDataset("aga")),
                    MergeDatasets(),
                ]
            ),
            "adg": Pipeline(
                [MapDict(StandardizeSignalDataset("adg")), MergeDatasets()]
            ),
            "ahx": Pipeline(
                [MapDict(StandardizeSignalDataset("ahx")), MergeDatasets()]
            ),
            "aim": Pipeline(
                [MapDict(StandardizeSignalDataset("aim")), MergeDatasets()]
            ),
            "air": Pipeline(
                [
                    MapDict(StandardizeSignalDataset("air")),
                    MergeDatasets(),
                ]
            ),
            "ait": Pipeline(
                [MapDict(StandardizeSignalDataset("ait")), MergeDatasets()]
            ),
            "alp": Pipeline(
                [
                    MapDict(DropZeroDimensions()),
                    MapDict(RenameDimensions()),
                    MapDict(StandardizeSignalDataset("alp")),
                    MergeDatasets(),
                ]
            ),
            "ama": Pipeline(
                [MapDict(StandardizeSignalDataset("ama")), MergeDatasets()]
            ),
            "amb": Pipeline(
                [
                    MapDict(StandardizeSignalDataset("abm")),
                    MergeDatasets(),
                    TensorizeChannels("ccbv"),
                    TensorizeChannels("obr"),
                    TensorizeChannels("obv"),
                    TensorizeChannels("fl_cc"),
                    TensorizeChannels("fl_p"),
                ]
            ),
            "amc": Pipeline(
                [MapDict(StandardizeSignalDataset("amc")), MergeDatasets()]
            ),
            "amh": Pipeline(
                [MapDict(StandardizeSignalDataset("amh")), MergeDatasets()]
            ),
            "amm": Pipeline(
                [
                    MapDict(StandardizeSignalDataset("amm")),
                    MergeDatasets(),
                    TensorizeChannels("incon"),
                    TensorizeChannels("mid"),
                    TensorizeChannels("ring"),
                    TensorizeChannels("rodgr"),
                    TensorizeChannels("vertw"),
                    TensorizeChannels("lhorw"),
                    TensorizeChannels("uhorw"),
                ]
            ),
            "ams": Pipeline(
                [MapDict(StandardizeSignalDataset("ams")), MergeDatasets()]
            ),
            "anb": Pipeline(
                [MapDict(StandardizeSignalDataset("amb")), MergeDatasets()]
            ),
            "ane": Pipeline(
                [MapDict(StandardizeSignalDataset("ane")), MergeDatasets()]
            ),
            "ant": Pipeline(
                [MapDict(StandardizeSignalDataset("ant")), MergeDatasets()]
            ),
            "anu": Pipeline(
                [MapDict(StandardizeSignalDataset("anu")), MergeDatasets()]
            ),
            "aoe": Pipeline(
                [
                    MapDict(RenameDimensions()),
                    MapDict(StandardizeSignalDataset("aoe")),
                    MergeDatasets(),
                ]
            ),
            "arp": Pipeline(
                [MapDict(StandardizeSignalDataset("arp")), MergeDatasets()]
            ),
            "asb": Pipeline(
                [MapDict(StandardizeSignalDataset("asb")), MergeDatasets()]
            ),
            "asm": Pipeline(
                [
                    MapDict(StandardizeSignalDataset("asm")),
                    MergeDatasets(),
                    TensorizeChannels("sad_m"),
                ]
            ),
            "asx": Pipeline(
                [
                    MapDict(ASXTransform()),
                    MapDict(StandardizeSignalDataset("asx")),
                    MergeDatasets(),
                ]
            ),
            "ayc": Pipeline(
                [MapDict(StandardizeSignalDataset("ayc")), MergeDatasets()]
            ),
            "aye": Pipeline(
                [MapDict(StandardizeSignalDataset("aye")), MergeDatasets()]
            ),
            "efm": Pipeline(
                [
                    DropDatasets(
                        [
                            "fcoil_n",
                            "fcoil_segs_n",
                            "limitern",
                            "magpr_n",
                            "silop_n",
                            "shot_number",
                        ]
                    ),
                    MapDict(RenameDimensions()),
                    MapDict(DropZeroDimensions()),
                    MapDict(StandardizeSignalDataset("efm")),
                    MergeDatasets(),
                ]
            ),
            "esm": Pipeline(
                [
                    MapDict(RenameDimensions()),
                    MapDict(DropZeroDimensions()),
                    MapDict(StandardizeSignalDataset("esm")),
                    MergeDatasets(),
                ]
            ),
            "esx": Pipeline(
                [MapDict(StandardizeSignalDataset("esx")), MergeDatasets()]
            ),
            "xdc": Pipeline(
                [
                    MapDict(StandardizeSignalDataset("xdc")),
                    MergeDatasets(),
                    TensorizeChannels(
                        "ai_cpu1_ccbv", dim_name="ai_ccbv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu1_flcc", dim_name="ai_flcc_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu1_incon",
                        dim_name="ai_incon_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu1_lhorw",
                        dim_name="ai_lhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu1_mid", dim_name="ai_mid_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu1_obr", dim_name="ai_obr_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu1_obv", dim_name="ai_obv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu1_ring", dim_name="ai_ring_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu1_rodgr",
                        dim_name="ai_rodgr_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu1_uhorw",
                        dim_name="ai_uhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu1_vertw",
                        dim_name="ai_vertw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu2_ccbv", dim_name="ai_ccbv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu2_flcc", dim_name="ai_flcc_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu2_incon",
                        dim_name="ai_incon_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu2_lhorw",
                        dim_name="ai_lhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu2_mid", dim_name="ai_mid_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu2_obr", dim_name="ai_obr_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu2_obv", dim_name="ai_obv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu2_ring", dim_name="ai_ring_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu2_rodgr",
                        dim_name="ai_rodgr_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu2_uhorw",
                        dim_name="ai_uhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu2_vertw",
                        dim_name="ai_vertw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu3_ccbv", dim_name="ai_ccbv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu3_flcc", dim_name="ai_flcc_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu3_incon",
                        dim_name="ai_incon_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu3_lhorw",
                        dim_name="ai_lhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu3_mid", dim_name="ai_mid_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu3_obr", dim_name="ai_obr_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu3_obv", dim_name="ai_obv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu3_ring", dim_name="ai_ring_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu3_rodgr",
                        dim_name="ai_rodgr_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu3_uhorw",
                        dim_name="ai_uhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu3_vertw",
                        dim_name="ai_vertw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu4_ccbv", dim_name="ai_ccbv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu4_flcc", dim_name="ai_flcc_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu4_incon",
                        dim_name="ai_incon_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu4_lhorw",
                        dim_name="ai_lhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu4_mid", dim_name="ai_mid_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu4_obr", dim_name="ai_obr_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu4_obv", dim_name="ai_obv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu4_ring", dim_name="ai_ring_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_cpu4_rodgr",
                        dim_name="ai_rodgr_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu4_uhorw",
                        dim_name="ai_uhorw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_cpu4_vertw",
                        dim_name="ai_vertw_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "ai_raw_ccbv", dim_name="ai_ccbv", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_raw_flcc", dim_name="ai_flcc_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_raw_obv", dim_name="ai_obv_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "ai_raw_obr", dim_name="ai_obr_channel", assign_coords=False
                    ),
                    TensorizeChannels(
                        "equil_s_seg",
                        regex=r"equil_s_seg(\d+)$",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "equil_s_seg_at",
                        regex=r"equil_s_seg(\d+)at$",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "equil_s_seg_rt",
                        regex=r"equil_s_seg(\d+)rt$",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "equil_s_seg_zt",
                        regex=r"equil_s_seg(\d+)zt$",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "equil_s_segb",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "equil_t_seg",
                        regex=r"equil_t_seg(\d+)$",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels(
                        "equil_t_seg_u",
                        regex=r"equil_t_seg(\d+)u$",
                        dim_name="equil_seg_channel",
                        assign_coords=False,
                    ),
                    TensorizeChannels("isoflux_e_seg"),
                    TensorizeChannels(
                        "isoflux_t_rpsh_n",
                        regex=r"isoflux_t_rpsh(\d+)n",
                    ),
                    TensorizeChannels(
                        "isoflux_t_rpsh_p",
                        regex=r"isoflux_t_rpsh(\d+)p",
                    ),
                    TensorizeChannels("isoflux_t_seg", regex=r"isoflux_t_seg(\d+)$"),
                    TensorizeChannels(
                        "isoflux_t_seg_gd", regex=r"isoflux_t_seg(\d+)gd$"
                    ),
                    TensorizeChannels(
                        "isoflux_t_seg_gi", regex=r"isoflux_t_seg(\d+)gi$"
                    ),
                    TensorizeChannels(
                        "isoflux_t_seg_gp", regex=r"isoflux_t_seg(\d+)gp$"
                    ),
                    TensorizeChannels(
                        "isoflux_t_seg_td", regex=r"isoflux_t_seg(\d+)td$"
                    ),
                    TensorizeChannels(
                        "isoflux_t_seg_ti", regex=r"isoflux_t_seg(\d+)ti$"
                    ),
                    TensorizeChannels(
                        "isoflux_t_seg_tp", regex=r"isoflux_t_seg(\d+)tp$"
                    ),
                    TensorizeChannels("isoflux_t_seg_u", regex=r"isoflux_t_seg(\d+)u$"),
                    TensorizeChannels(
                        "isoflux_t_zpsh_n",
                        regex=r"isoflux_t_zpsh(\d+)n",
                    ),
                    TensorizeChannels(
                        "isoflux_t_zpsh_p",
                        regex=r"isoflux_t_zpsh(\d+)p",
                    ),
                ]
            ),
            "xpc": Pipeline(
                [
                    MapDict(RenameDimensions()),
                    MapDict(StandardizeSignalDataset("xpc")),
                    MergeDatasets(),
                ]
            ),
            "xsx": Pipeline(
                [
                    MapDict(StandardizeSignalDataset("xsx")),
                    MergeDatasets(),
                    TensorizeChannels("hcam_l", regex=r"hcam_l_(\d+)"),
                    TensorizeChannels("hcam_u", regex=r"hcam_u_(\d+)"),
                    TensorizeChannels("tcam", regex=r"tcam_(\d+)"),
                ]
            ),
        }

    def get(self, name: str) -> Pipeline:
        if name not in self.pipelines:
            raise RuntimeError(f"{name} is not a registered source!")
        return self.pipelines[name]
