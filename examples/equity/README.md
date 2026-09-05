# equity

A qanat project. Five stages, and the last one is a portfolio.

    qanat check      # does the pipeline obey the stage contract?
    qanat run        # poll every source, run every step, once
    qanat serve      # scheduler + console on http://127.0.0.1:8420

The sources are synthetic, so this runs with no network and no keys. Point
`sources:` at a real feed when you have one -- nothing else has to change.
