"""Parser fixture only; parameter values deliberately differ."""

import NXOpen
import NXOpen.CAM


def main():
    the_session = NXOpen.Session.GetSession()
    part = the_session.Parts.Work
    cam = part.CAMSetup
    target_program = cam.CAMGroupCollection.FindObject("PROGRAM")
    cutting_method = cam.CAMGroupCollection.FindObject("METHOD")
    selected_tool = cam.CAMGroupCollection.FindObject("TOOL_12")
    workpiece = cam.CAMGroupCollection.FindObject("MCS_WORKPIECE")
    face_mill = cam.CAMOperationCollection.Create(
        target_program,
        cutting_method,
        selected_tool,
        workpiece,
        "mill_planar",
        "FACE_MILLING",
    )
    face_mill.Tolerance = 0.03
    face_mill.Commit()
    cam.GenerateToolPath([face_mill])


if __name__ == "__main__":
    main()

