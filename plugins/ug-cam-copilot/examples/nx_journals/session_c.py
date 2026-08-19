"""Parser fixture only; aliases and tool names differ."""

import NXOpen
import NXOpen.CAM


def main():
    nx = NXOpen.Session.GetSession()
    wp = nx.Parts.Work
    cam_setup = wp.CAMSetup
    p = cam_setup.CAMGroupCollection.FindObject("PROGRAM_MAIN")
    m = cam_setup.CAMGroupCollection.FindObject("METHOD_ROUGH")
    t = cam_setup.CAMGroupCollection.FindObject("TOOL_FACE_50")
    g = cam_setup.CAMGroupCollection.FindObject("GEOMETRY_WORKPIECE")
    op = cam_setup.CAMOperationCollection.Create(
        p, m, t, g, "mill_planar", "FACE_MILLING"
    )
    op.Tolerance = 0.015
    op.Commit()
    cam_setup.GenerateToolPath([op])


if __name__ == "__main__":
    main()

