"""Run the report pipeline from saved evidence."""

if __package__:
    from .run_rewrite import load_evidence, main, run_writing_pipeline
else:
    from run_rewrite import load_evidence, main, run_writing_pipeline

__all__ = ["load_evidence", "main", "run_writing_pipeline"]


if __name__ == "__main__":
    main()
