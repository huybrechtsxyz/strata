SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
DOCSDIR       ?= docs/
BUILDDIR      ?= documentation_build
TARGETDIR     ?= html_docs
VENVDIR		  ?= ./.doc_venv

.PHONY: all

all: clean build

build: make_doc_venv badges build_docs move_to_target clean_build

make_doc_venv:
	@uv venv "$(VENVDIR)"
	@. "$(VENVDIR)"/bin/activate && uv pip install . --group dev --group doc

badges:
	@"$(VENVDIR)"/bin/coverage-badge > "$(DOCSDIR)"/_static/coverage.svg || (echo "Error: pytest coverage information is not found, please run pytest"; exit 1)
	@"$(VENVDIR)"/bin/python -m anybadge --value=`cat VERSION.txt` --color=blue --file="$(DOCSDIR)"/_static/version.svg -l python
	
build_docs:	
	@pandoc README.md -o "$(DOCSDIR)"/README.rst
	@"$(VENVDIR)"/bin/$(SPHINXBUILD) -M html "$(DOCSDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(0)

move_to_target:
	@mv "$(BUILDDIR)/html" "$(TARGETDIR)"
	
clean_build:	
	@rm -Rf "$(BUILDDIR)"

clean:
	@rm -Rf "$(TARGETDIR)"
	@rm -f "$(DOCSDIR)"/_static/coverage.svg
	@rm -f "$(DOCSDIR)"/_static/version.svg
	@rm -f "$(DOCSDIR)"/README.rst
	@rm -Rf "$(VENVDIR)"
