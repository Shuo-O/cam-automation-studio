"""Incomplete Builder lifecycle fixture. Never import or execute this file."""

import NXOpen
import NXOpen.CAM


def main():
    session = NXOpen.Session.GetSession()
    work_part = session.Parts.Work
    builder = work_part.CAMSetup.CAMOperationCollection.CreatePlanarMillingBuilder(
        None
    )
    builder.Tolerance = 0.05
