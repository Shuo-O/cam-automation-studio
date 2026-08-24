"""Static FlowGraph fixture. Never import or execute this Journal."""

import NXOpen
import NXOpen.CAM


def main():
    session = NXOpen.Session.GetSession()
    work_part = session.Parts.Work
    mark_id = session.SetUndoMark(
        NXOpen.Session.MarkVisibility.Visible, "Review Builder"
    )
    program = work_part.CAMSetup.CAMGroupCollection.FindObject("NC_PROGRAM")
    builder = work_part.CAMSetup.CAMOperationCollection.CreatePlanarMillingBuilder(
        None
    )
    if program is None:
        builder.Tolerance = 0.02
    else:
        builder.Tolerance = 0.03
    try:
        operation = builder.Commit()
        operation.SetName("REVIEW_ONLY")
    except NXOpen.NXException:
        raise
    finally:
        builder.Destroy()
    session.DeleteUndoMark(mark_id, False)


if __name__ == "__main__":
    main()
