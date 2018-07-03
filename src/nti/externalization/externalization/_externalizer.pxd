# definitions for externalization.pxd
import cython

from nti.externalization.externalization._dictionary cimport internal_to_standard_external_dictionary



# Imports
cdef collections
cdef defaultdict
cdef six
cdef numbers

cdef component
cdef IFiniteSequence

cdef ThreadLocalManager
cdef get_current_request
cdef set_external_identifiers
cdef IExternalMappingDecorator
cdef IExternalObject
cdef IExternalObjectDecorator
cdef ILocatedExternalSequence
cdef INonExternalizableReplacement
cdef INonExternalizableReplacer
cdef DefaultNonExternalizableReplacer
cdef NotGiven


# Constants
cdef logger

cdef _manager, _manager_get, _manager_pop, _manager_push
cpdef tuple PRIMITIVES
cdef _marker

cdef tuple SEQUENCE_TYPES
cdef tuple MAPPING_TYPES



@cython.final
@cython.internal
@cython.freelist(1000)
cdef class _ExternalizationState(object):
    cdef dict memo

    cdef basestring name
    cdef registry
    cdef catch_components
    cdef catch_component_action
    cdef request
    cdef default_non_externalizable_replacer


# can't use freelist on subclass
@cython.final
@cython.internal
cdef class _RecursiveCallState(dict):
    pass


#@cython.locals(
#)
cpdef to_external_object(
    obj,
    name=*,
    registry=*,
    catch_components=*,
    catch_component_action=*,
    request=*,
    bint decorate=*,
    bint useCache=*,
    decorate_callback=*,
    default_non_externalizable_replacer=*
)

@cython.locals(
    obj_has_usable_external_object=bint,
)
cdef _to_external_object_state(obj, _ExternalizationState state,
                               bint top_level=*,
                               bint decorate=*,
                               bint useCache=*,
                               decorate_callback=*)
