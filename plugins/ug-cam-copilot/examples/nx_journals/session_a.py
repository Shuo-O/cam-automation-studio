"""Parser fixture only; this file is not intended to run in NX."""

import NXOpen
import NXOpen.CAM


def main():
    session = NXOpen.Session.GetSession()
    work_part = session.Parts.Work
    setup = work_part.CAMSetup
    program = setup.CAMGroupCollection.FindObject("PROGRAM")
    method = setup.CAMGroupCollection.FindObject("METHOD")
    tool = setup.CAMGroupCollection.FindObject("TOOL_10")
    geometry = setup.CAMGroupCollection.FindObject("WORKPIECE")
    operation = setup.CAMOperationCollection.Create(
        program, method, tool, geometry, "mill_planar", "FACE_MILLING"
    )
    operation.Tolerance = 0.02
    operation.Commit()
    setup.GenerateToolPath([operation])
    session.SetUndoMarkName(1, "recorder noise")


if __name__ == "__main__":
    main()

