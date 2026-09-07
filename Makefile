.PHONY: install study tests reproduce app serve docker clean

install:
	pip install -r requirements.txt

study:            ## Part I then Part II: regenerates every figure and result table
	python src/scenarios.py
	python src/studies.py

tests:            ## the four test scripts, in the order they are cheapest to fail
	python tests/test_spec_compliance.py
	python tests/test_rollout.py
	python tests/test_reported_numbers.py
	python tests/test_external_simulator.py

reproduce:        ## delete the outputs, rebuild them, prove they come back identical
	rm -rf results_committed && cp -r results results_committed
	rm -rf results figures
	$(MAKE) study
	python tools/check_reproducibility.py results_committed results
	rm -rf results_committed

app:              ## interactive demo at http://localhost:8501
	streamlit run app/streamlit_app.py

serve:            ## HTTP service with docs at http://localhost:8000/docs
	uvicorn service.api:app --reload --port 8000

docker:
	docker build -t choke-controller .

clean:
	rm -rf __pycache__ src/__pycache__ tests/__pycache__ results_committed
	find . -name '*.pyc' -delete
