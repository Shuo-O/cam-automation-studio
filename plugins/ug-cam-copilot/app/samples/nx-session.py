"""Static parser sample. It is not intended to execute inside NX."""

import NXOpen
import NXOpen.CAM


def main():
    session = NXOpen.Session.GetSession()
    work_part = session.Parts.Work
    setup = work_part.CAMSetup

    program_a = setup.CAMGroupCollection.FindObject("PROGRAM_A")
    method_a = setup.CAMGroupCollection.FindObject("METHOD_A")
    tool_a = setup.CAMGroupCollection.FindObject("TOOL_10")
    geometry_a = setup.CAMGroupCollection.FindObject("WORKPIECE_A")
    operation_a = setup.CAMOperationCollection.Create(
        program_a, method_a, tool_a, geometry_a, "mill_planar", "FACE_MILLING"
    )
    operation_a.Tolerance = 0.02
    operation_a.Commit()
    setup.GenerateToolPath([operation_a])

    program_b = setup.CAMGroupCollection.FindObject("PROGRAM_B")
    method_b = setup.CAMGroupCollection.FindObject("METHOD_B")
    tool_b = setup.CAMGroupCollection.FindObject("TOOL_12")
    geometry_b = setup.CAMGroupCollection.FindObject("WORKPIECE_B")
    operation_b = setup.CAMOperationCollection.Create(
        program_b, method_b, tool_b, geometry_b, "mill_planar", "FACE_MILLING"
    )
    operation_b.Tolerance = 0.03
    operation_b.Commit()
    setup.GenerateToolPath([operation_b])


if __name__ == "__main__":
    main()

