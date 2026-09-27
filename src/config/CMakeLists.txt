# Copyright (c) Lawrence Livermore National Security, LLC and other Conduit
# Project developers. See top-level LICENSE AND COPYRIGHT files for dates and
# other details. No copyright assignment is required to contribute to Conduit.

###############################################################################
# create config.mk for makefile-based build systems
###############################################################################

configure_file ("${CMAKE_CURRENT_SOURCE_DIR}/conduit_config.mk.in"
                "${CMAKE_CURRENT_BINARY_DIR}/conduit_config.mk")

# install our config.mk
install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/conduit_config.mk"
        DESTINATION share/conduit/)

###############################################################################
# export everything for a cmake build to be able to import with find_package
###############################################################################

if (NOT DEFINED CONDUIT_INSTALL_INCLUDE_DIR)
    set(CONDUIT_INSTALL_INCLUDE_DIR "include")
endif()

if (NOT DEFINED CONDUIT_INSTALL_CONFIG_DIR)
    set(CONDUIT_INSTALL_CONFIG_DIR "lib")
endif()

if (NOT DEFINED CONDUIT_INSTALL_LIB_DIR)
    set(CONDUIT_INSTALL_LIB_DIR "lib")
endif()

if (NOT DEFINED CONDUIT_INSTALL_BIN_DIR)
    set(CONDUIT_INSTALL_BIN_DIR "bin")
endif()

if (NOT DEFINED CONDUIT_INSTALL_SHARED_RESOURCES_DIR)
  set(CONDUIT_INSTALL_SHARED_RESOURCES_DIR "share/conduit")
endif()

if (NOT DEFINED CONDUIT_INSTALL_CMAKE_MODULE_DIR)
  set(CONDUIT_INSTALL_CMAKE_MODULE_DIR "${CONDUIT_INSTALL_CONFIG_DIR}/cmake")
endif()

if (NOT DEFINED CONDUIT_INSTALL_PYTHON_MODULE_DIR)
    if(PYTHON_MODULE_INSTALL_PREFIX)
        set(CONDUIT_INSTALL_PYTHON_MODULE_DIR ${PYTHON_MODULE_INSTALL_PREFIX})
        set(CONDUIT_INSTALL_PYTHON_MODULE_CUSTOM_PREFIX ON)
    else()
        set(CONDUIT_INSTALL_PYTHON_MODULE_DIR "python-modules/")
        set(CONDUIT_INSTALL_PYTHON_MODULE_CUSTOM_PREFIX OFF)
    endif()
endif()


include(CMakePackageConfigHelpers)

# write version heler
write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/ConduitConfigVersion.cmake
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY AnyNewerVersion
)


###############################################################################
# relocatable installs: third-party install dirs that live inside our
# install prefix (i.e. bundled TPLs) are recorded relative to the prefix.
# ConduitConfig.cmake expands ${_IMPORT_ROOT} to the actual install root.
###############################################################################
if(ENABLE_RELOCATABLE_INSTALL)
    get_filename_component(_conduit_prefix_real "${CMAKE_INSTALL_PREFIX}" REALPATH)
    string(LENGTH "${_conduit_prefix_real}" _conduit_prefix_len)
    foreach(_var CONDUIT_INSTALL_PREFIX HDF5_DIR ZLIB_DIR)
        if(${_var})
            get_filename_component(_var_real "${${_var}}" REALPATH)
            string(FIND "${_var_real}" "${_conduit_prefix_real}" _pos)
            if(_pos EQUAL 0)
                string(SUBSTRING "${_var_real}" ${_conduit_prefix_len} -1 _rest)
                # shadow the cache var for configure_file below
                set(${_var} "\${_IMPORT_ROOT}${_rest}")
                message(STATUS "Relocatable config: ${_var} = ${${_var}}")
            endif()
        endif()
    endforeach()
endif()

# setup cmake package config file
configure_package_config_file(
  ConduitConfig.cmake.in
  ${CMAKE_CURRENT_BINARY_DIR}/ConduitConfig.cmake
  INSTALL_DESTINATION ${CONDUIT_INSTALL_CONFIG_DIR}
  PATH_VARS
    CONDUIT_INSTALL_INCLUDE_DIR
    CONDUIT_INSTALL_LIB_DIR
    CONDUIT_INSTALL_BIN_DIR
    CONDUIT_INSTALL_SHARED_RESOURCES_DIR
    CONDUIT_INSTALL_CMAKE_MODULE_DIR
    )


# install everything needed

# install exports to comply with standard find_package search path expectations
install(FILES
        ${CMAKE_CURRENT_BINARY_DIR}/ConduitConfig.cmake
        ${CMAKE_CURRENT_BINARY_DIR}/ConduitConfigVersion.cmake
        conduit_setup_deps.cmake
        conduit_setup_targets.cmake
        DESTINATION ${CONDUIT_INSTALL_CMAKE_MODULE_DIR}/${CONDUIT_CMAKE_PACKAGE_DIR}/)


