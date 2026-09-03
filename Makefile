.PHONY: verify verify-links

verify: verify-links
	python3 -B release/verify_analysis.py

verify-links:
	python3 -B release/verify_links.py
