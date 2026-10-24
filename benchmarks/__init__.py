"""
Multi-website benchmark harness for VerityQA.

`targets` describes what the autonomous agent is given (URL, a user-style description, the
application's published requirements, authorized demo credentials). `ground_truth` holds the
planted-defect oracle and is imported only by `evaluator`, after the agent has finished.
"""
