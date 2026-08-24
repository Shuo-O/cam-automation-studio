"""Realistic NX recorder-style fixture. Never import or execute this file."""

import NXOpen
import NXOpen.CAM


def main():
    the_session = NXOpen.Session.GetSession()
    work_part = the_session.Parts.Work
    mark_id = the_session.SetUndoMark(
        NXOpen.Session.MarkVisibility.Visible, "Create Planar Milling"
    )
    program = work_part.CAMSetup.CAMGroupCollection.FindObject("NC_PROGRAM")
    builder = work_part.CAMSetup.CAMOperationCollection.CreatePlanarMillingBuilder(
        None
    )
    builder.Tolerance = 0.025
    builder.CutParameters.Stock = 0.2
    try:
        operation = builder.Commit()
        operation.SetName("PLANAR_REVIEW")
    except NXOpen.NXException:
        raise
    finally:
        builder.Destroy()
    the_session.SetUndoMarkName(mark_id, "Create Planar Milling")


if __name__ == "__main__":
    main()
