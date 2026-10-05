# Project adapter for the shared simulation flow. Module Makefiles keep
# including $(PROJECT_ROOT)/test/Makefile, while all flow logic lives in
# IC-RTL-COMMON.
PROJECT_ROOT ?=
ifeq ($(PROJECT_ROOT),)
  PROJECT_ROOT := $(abspath $(dir $(abspath $(lastword $(MAKEFILE_LIST))))/..)
endif

RTL_COMMONS_ROOT ?=
ifeq ($(RTL_COMMONS_ROOT),)
  RTL_COMMONS_ROOT := $(PROJECT_ROOT)/package/rtl-commons
endif

ifeq ($(wildcard $(RTL_COMMONS_ROOT)/simulation/Makefile),)
  $(error Shared simulation Makefile not found: $(RTL_COMMONS_ROOT)/simulation/Makefile. Run git submodule update --init --recursive.)
endif

include $(RTL_COMMONS_ROOT)/simulation/Makefile
