# cython: auto_pickle=False,embedsignature=True,always_allow_keywords=False
# -*- coding: utf-8 -*-
"""
The driver functions for updating an object from an external form.

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


# stdlib imports
try:
    from collections.abc import MutableSequence
    from collections.abc import MutableMapping
except ImportError:
    from collections import MutableSequence
    from collections import MutableMapping
import inspect
import warnings

from persistent.interfaces import IPersistent
from six import iteritems
from zope import component
from zope import interface

from nti.externalization._base_interfaces import PRIMITIVES
from nti.externalization.interfaces import IInternalObjectUpdater
from nti.externalization.interfaces import IInternalObjectFactoryFinder

from .factories import find_factory_for
from .events import _notifyModified
from .externals import resolve_externals



_EMPTY_DICT = {}
IPersistent_providedBy = IPersistent.providedBy

class _RecallArgs(object):
    __slots__ = (
        'registry',
        'context',
        'require_updater',
        'notify',
        'pre_hook',
    )

    # We don't have an __init__, we ask the caller
    # to fill us in. In cython, this avoids some
    # unneeded bint->object->bint conversions.

    def __init__(self):
        self.registry = None
        self.context = None
        self.require_updater = False
        self.notify = True
        self.pre_hook = None


def _recall(k, obj, ext_obj, kwargs):
    # We must manually pass all the args to get the optimized
    # cython call
    obj = update_from_external_object(obj, ext_obj,
                                      registry=kwargs.registry,
                                      context=kwargs.context,
                                      require_updater=kwargs.require_updater,
                                      notify=kwargs.notify,
                                      pre_hook=kwargs.pre_hook)
    if IPersistent_providedBy(obj): # pragma: no cover
        obj._v_updated_from_external_source = ext_obj # pylint:disable=protected-access
    return obj

##
# Note on caching: We do not expect the updater objects to be proxied.
# So we directly use type() instead of .__class__, which is faster.
# We also do not expect them to be unloaded/updated/unbounded,
# so we use a regular dict to cache info about them, which is faster
# than a WeakKeyDictionary. For the same reason, we use dynamic warning
# strings.

# Support for varying signatures of the updater. This is slow and
# cumbersome and needs to go; we are in the deprecation period now.
# See https://github.com/NextThought/nti.externalization/issues/30

_argspec_cache = {}

# update(ext, context) or update(ext, context=None) or update(ext, dataserver)
# exactly two arguments. It doesn't matter what the name is, we'll call it
# positional.
_UPDATE_ARGS_TWO = "update args two"
_UPDATE_ARGS_CONTEXT_KW = "update args **kwargs"
_UPDATE_ARGS_ONE = "update args external only"


def _get_update_signature(updater):
    kind = type(updater)

    spec = _argspec_cache.get(kind)
    if spec is None:
        try:
            func = updater.updateFromExternalObject
            if hasattr(inspect, 'getfullargspec'): # pragma: no cover
                # Python 3. getargspec() is deprecated.
                argspec = inspect.getfullargspec(func) # pylint:disable=no-member
                keywords = argspec.varkw
            else:
                argspec = inspect.getargspec(func)
                keywords = argspec.keywords
            args = argspec.args
            defaults = argspec.defaults
        except TypeError: # pragma: no cover (This is hard to catch in pure-python coverage mode)
            # Cython functions and other extension types are "not a Python function"
            # and don't work with this. We assume they use the standard form accepting
            # 'context' as kwarg
            spec = _UPDATE_ARGS_CONTEXT_KW
        else:
            # argspec.args contains the names of all the parameters.
            # argspec.keywords, if not none, is the name of the **kwarg
            # These all must be methods (or at least classmethods), having
            # an extra 'self' argument.
            if not keywords:
                # No **kwarg, good!
                if len(args) == 3:
                    # update(ext, context) or update(ext, context=None) or update(ext, dataserver)
                    spec = _UPDATE_ARGS_TWO
                else:
                    # update(ext)
                    spec = _UPDATE_ARGS_ONE
            else:
                if len(args) == 3:
                    # update(ext, context, **kwargs) or update(ext, dataserver, **kwargs)
                    spec = _UPDATE_ARGS_TWO
                elif keywords.startswith("unused") or keywords.startswith('_'):
                    spec = _UPDATE_ARGS_ONE
                else:
                    spec = _UPDATE_ARGS_CONTEXT_KW

            if 'dataserver' in args and defaults and len(defaults) >= 1:
                warnings.warn("The type %r still uses updateFromExternalObject(dataserver=None). "
                              "Please change to context=None." % (kind,),
                              FutureWarning)

        _argspec_cache[kind] = spec

    return spec


_usable_updateFromExternalObject_cache = {}

def _obj_has_usable_updateFromExternalObject(obj):
    kind = type(obj)

    usable_from = _usable_updateFromExternalObject_cache.get(kind)
    if usable_from is None:
        has_update = hasattr(obj, 'updateFromExternalObject')
        if not has_update:
            usable_from = False
        else:
            wants_ignore = getattr(obj, '__ext_ignore_updateFromExternalObject__', False)
            usable_from = not wants_ignore
            if wants_ignore:
                warnings.warn("The type %r has __ext_ignore_updateFromExternalObject__=True. "
                              "Please remove updateFromExternalObject from the type." % (kind,),
                              FutureWarning)


        _usable_updateFromExternalObject_cache[kind] = usable_from

    return usable_from


try:
    from zope.testing import cleanup # pylint:disable=ungrouped-imports
except ImportError: # pragma: no cover
    raise
else:
    cleanup.addCleanUp(_argspec_cache.clear)
    cleanup.addCleanUp(_usable_updateFromExternalObject_cache.clear)


class DefaultInternalObjectFactoryFinder(object):

    def get_object_to_update(self, key, value, registry):
        return find_factory_for(value, registry)


interface.classImplements(DefaultInternalObjectFactoryFinder, IInternalObjectFactoryFinder)

_default_factory_finder = DefaultInternalObjectFactoryFinder()

def update_from_external_object(containedObject, externalObject,
                                registry=component, context=None,
                                require_updater=False,
                                notify=True,
                                pre_hook=None):
    """
    Central method for updating objects from external values.

    :param containedObject: The object to update.
    :param externalObject: The object (typically a mapping or sequence) to update
        the object from. Usually this is obtained by parsing an external
        format like JSON.
    :param context: An object passed to the update methods.
    :param require_updater: If True (not the default) an exception
        will be raised if no implementation of
        :class:`~nti.externalization.interfaces.IInternalObjectUpdater`
        can be found for the `containedObject.`
    :keyword bool notify: If ``True`` (the default), then if the updater
        for the `containedObject` either has no preference (returns
        None) or indicates that the object has changed, then an
        :class:`~nti.externalization.interfaces.IObjectModifiedFromExternalEvent`
        will be fired. This may be a recursive process so a top-level
        call to this object may spawn multiple events. The events that
        are fired will have a ``descriptions`` list containing one or
        more :class:`~zope.lifecycleevent.interfaces.IAttributes` each
        with ``attributes`` for each attribute we modify (assuming
        that the keys in the ``externalObject`` map one-to-one to an
        attribute; if this is the case and we can also find an
        interface declaring the attribute, then the ``IAttributes``
        will have the right value for ``interface`` as well).
    :keyword callable pre_hook: If given, called with the before
        update_from_external_object is called for every nested object.
        Signature ``f(k,x)`` where ``k`` is either the key name, or
        None in the case of a sequence and ``x`` is the external
        object. Deprecated.
    :return: *containedObject* after updates from *externalObject*

    .. versionchanged:: 1.0.0a2
       Remove the ``object_hook`` parameter.
    """

    if pre_hook is not None: # pragma: no cover
        for i in range(3):
            warnings.warn('pre_hook is deprecated', FutureWarning, stacklevel=i)

    kwargs = _RecallArgs()
    kwargs.registry = registry
    kwargs.context = context
    kwargs.require_updater = require_updater
    kwargs.notify = notify
    kwargs.pre_hook = pre_hook


    # Parse any contained objects
    # TODO: We're (deliberately?) not actually updating any contained
    # objects, we're replacing them. Is that right? We could check OIDs...
    # If we decide that's right, then the internals could be simplified by
    # splitting the two parts
    # TODO: Schema validation
    # TODO: Should the current user impact on this process?

    # Sequences do not represent python types, they represent collections of
    # python types
    if isinstance(externalObject, MutableSequence):
        tmp = []
        for value in externalObject:
            if pre_hook is not None: # pragma: no cover
                pre_hook(None, value)
            factory = find_factory_for(value, registry=registry)
            tmp.append(_recall(None, factory(), value, kwargs) if factory is not None else value)
        # XXX: TODO: Should we be assigning this to the slice of externalObject?
        # in-place?
        return tmp

    assert isinstance(externalObject, MutableMapping)

    updater = registry.queryAdapter(containedObject, IInternalObjectFactoryFinder,
                                    u'', _default_factory_finder)
    get_object_to_update = updater.get_object_to_update

    # We have to save the list of keys, it's common that they get popped during the update
    # process, and then we have no descriptions to send
    external_keys = list()
    for k, v in iteritems(externalObject):
        external_keys.append(k)
        if isinstance(v, PRIMITIVES):
            continue

        if pre_hook is not None: # pragma: no cover
            pre_hook(k, v)

        if isinstance(v, MutableSequence):
            # Update the sequence in-place
            # XXX: This is not actually updating it.
            # We need to slice externalObject[k[:]]
            __traceback_info__ = k, v
            v = _recall(k, (), v, kwargs)
            externalObject[k] = v
        else:
            factory = get_object_to_update(k, v, registry)
            if factory is not None:
                externalObject[k] = _recall(k, factory(), v, kwargs)


    if _obj_has_usable_updateFromExternalObject(containedObject):
        # legacy support. The __ext_ignore_updateFromExternalObject__
        # allows a transition to an adapter without changing
        # existing callers and without triggering infinite recursion
        updater = containedObject
    else:
        if not IInternalObjectUpdater.providedBy(updater):
            if require_updater:
                get = registry.getAdapter
            else:
                get = registry.queryAdapter

            updater = get(containedObject, IInternalObjectUpdater)

    if updater is not None:
        # Let the updater resolve externals too
        resolve_externals(updater, containedObject, externalObject,
                          registry=registry, context=context)

        updated = None
        # The signature may vary.
        arg_kind = _get_update_signature(updater)
        if arg_kind is _UPDATE_ARGS_TWO:
            updated = updater.updateFromExternalObject(externalObject, context)
        elif arg_kind is _UPDATE_ARGS_ONE:
            updated = updater.updateFromExternalObject(externalObject)
        else:
            updated = updater.updateFromExternalObject(externalObject,
                                                       context=context)

        # Broadcast a modified event if the object seems to have changed.
        if notify and (updated is None or updated):
            _notifyModified(containedObject, externalObject,
                            updater, external_keys, _EMPTY_DICT)

    return containedObject



from nti.externalization._compat import import_c_accel # pylint:disable=wrong-import-position,wrong-import-order
import_c_accel(globals(), 'nti.externalization.internalization._updater')
