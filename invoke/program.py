import sys

import six

from .cli import *


class Program(object):
    """
    Manages top-level CLI invocation, typically via setup.py entrypoints.

    Designed for distributing Invoke task collections as standalone programs,
    but also used internally to implement the ``invoke`` program itself.

    .. seealso::
        :ref:`reusing-as-a-binary` for a tutorial/walkthrough of this
        functionality.
    """
    # Arguments present always, even when wrapped as a different binary
    core_args = (
        Argument(
            names=('complete',),
            kind=bool,
            default=False,
            help="Print tab-completion candidates for given parse remainder.", # noqa
        ),
        Argument(
            names=('debug', 'd'),
            kind=bool,
            default=False,
            help="Enable debug output.",
        ),
        Argument(
            names=('echo', 'e'),
            kind=bool,
            default=False,
            help="Echo executed commands before running.",
        ),
        Argument(
            names=('config', 'f'),
            help="Runtime configuration file to use.",
        ),
        Argument(
            names=('help', 'h'),
            optional=True,
            help="Show core or per-task help and exit."
        ),
        Argument(
            names=('hide', 'H'),
            help="Set default value of run()'s 'hide' kwarg.",
        ),
        Argument(
            names=('pty', 'p'),
            kind=bool,
            default=False,
            help="Use a pty when executing shell commands.",
        ),
        Argument(
            names=('version', 'V'),
            kind=bool,
            default=False,
            help="Show version and exit."
        ),
        Argument(
            names=('warn-only', 'w'),
            kind=bool,
            default=False,
            help="Warn, instead of failing, when shell commands fail.",
        ),
    )

    # Arguments pertaining specifically to invocation as 'invoke' itself (or as
    # other arbitrary-task-executing programs, like 'fab')
    task_args = (
        Argument(
            names=('collection', 'c'),
            help="Specify collection name to load."
        ),
        Argument(
            names=('list', 'l'),
            kind=bool,
            default=False,
            help="List available tasks."
        ),
        Argument(
            names=('no-dedupe',),
            kind=bool,
            default=False,
            help="Disable task deduplication."
        ),
        Argument(
            names=('root', 'r'),
            help="Change root directory used for finding task modules."
        ),
    )

    def __init__(self, version=None, namespace=None, name=None, binary=None):
        """
        Create a new, parameterized `.Program` instance.

        :param str version:
            The program's version, e.g. ``"0.1.0"``. Defaults to ``"unknown"``.

        :param namespace:
            A `.Collection` to use as this program's subcommands.

            If ``None`` (the default), the program will behave like ``invoke``,
            seeking a nearby task namespace with a `.Loader` and exposing
            arguments such as :option:`--list` and :option:`--collection` for
            inspecting or selecting specific namespaces.

            If given a `.Collection` object, will use it as if it had been
            handed to :option:`--collection`. Will also update the parser to
            remove references to tasks and task-related options, and display
            the subcommands in ``--help`` output. The result will be a program
            that has a static set of subcommands.

        :param str name:
            The program's name, as displayed in ``--version`` output.

            If ``None`` (default), is a capitalized version of the first word
            in the ``argv`` handed to `.run`. For example, when invoked from a
            binstub installed as ``foobar``, it will default to ``Foobar``.

        :param str binary:
            The binary name as displayed in ``--help`` output.

            If ``None`` (default), uses the first word in ``argv`` verbatim (as
            with ``name`` above, except not capitalized).

            Giving this explicitly may be useful when you install your program
            under multiple names, such as Invoke itself does - it installs as
            both ``inv`` and ``invoke``, and sets ``name="inv[oke]"`` so its
            ``--help`` output implies both names.
        """
        self.version = "unknown" if version is None else version
        self.namespace = namespace
        self._name = name
        self._binary = binary
        self.argv = None

    def config(self):
        """
        Generate a `.Config` object initialized with parser & collection data.

        Specifically, parser-level flags are consulted (typically as a
        top-level "runtime overrides" dict) and the `.Collection` object is
        used to determine where to seek a per-project config file.

        This object is further updated within `.Executor` with per-task
        configuration values and then told to load the full hierarchy (which
        includes config files.)
        """
        # Set up runtime overrides from flags.
        # NOTE: only fill in values that would alter behavior, otherwise we
        # want the defaults to come through.
        run = {}
        if self.args['warn-only'].value:
            run['warn'] = True
        if self.args.pty.value:
            run['pty'] = True
        if self.args.hide.value:
            run['hide'] = self.args.hide.value
        if self.args.echo.value:
            run['echo'] = True
        tasks = {}
        if self.args['no-dedupe'].value:
            tasks['dedupe'] = False
        overrides = {'run': run, 'tasks': tasks}
        # Stand up config object
        c = Config(
            overrides=overrides,
            project_home=self.collection.loaded_from,
            runtime_path=self.args.config.value,
            env_prefix='INVOKE_',
        )
        return c

    def run(self, argv=None, exit=True):
        """
        Execute main CLI logic, based on ``argv``.

        :param argv:
            The arguments to execute against. May be ``None``, a list of
            strings, or a string. See `.normalize_argv` for details.

        :param bool exit:
            When ``True`` (default: ``False``), will ignore `.ParseError`,
            `.Exit` and `.Failure` exceptions, which otherwise trigger calls to
            `sys.exit`.

            .. note::
                This is mostly a concession to testing. If you're setting this
                to ``True`` in a production setting, you should probably be
                using `.Executor` and friends directly instead!
        """
        debug("argv given to Program.run: {0!r}".format(argv))
        self.normalize_argv(argv)
        try:
            # Obtain core args (sets self.core)
            self.parse_core_args()
            debug("Finished parsing core args")

            # Enable debugging from here on out, if debug flag was given.
            # (Prior to this point, debugging requires setting INVOKE_DEBUG).
            if self.args.debug.value:
                enable_logging()

            # Print version & exit if necessary
            if self.args.version.value:
                debug("Saw --version, printing version & exiting")
                self.print_version()
                raise Exit

            # Core (no value given) --help output
            # TODO: if this wants to display context sensitive help (e.g. a
            # combo help and available tasks listing; or core flags modified by
            # plugins/task modules) it will have to move farther down.
            if self.args.help.value is True:
                debug("Saw bare --help, printing help & exiting")
                self.print_help()
                raise Exit

            # Load a collection of tasks unless one was already set.
            if self.namespace is not None:
                debug("Program was given a default namespace, skipping collection loading") # noqa
                self.collection = self.namespace
            else:
                debug("No default namespace provided, trying to load one from disk") # noqa
                self.load_collection()

            # Parse remainder into task contexts (sets
            # self.parser/collection/tasks)
            self.parse_tasks()

            # Print per-task help, if necessary
            if self.args.help.value in self.parser.contexts:
                msg = "Saw --help <taskname>, printing per-task help & exiting"
                debug(msg)
                self.print_task_help()
                raise Exit

            # Print discovered tasks if necessary
            if self.args.list.value:
                self.list_tasks()
                raise Exit

            # Print completion helpers if necessary
            if self.args.complete.value:
                # TODO: reference these within complete() after moving it here
                complete(self.core, self.initial_context, self.collection)

            # No tasks specified for execution & no default task = print help
            if not self.tasks and not self.collection.default:
                self.print_help()
                raise Exit

            executor = Executor(self.collection, self.config())
            tasks = tasks_from_contexts(self.tasks, self.collection)
            executor.execute(*tasks)
        except (Failure, Exit, ParseError) as e:
            debug("Received a possibly-skippable exception: {0!r}".format(e))
            # Print error message from parser if necessary.
            if isinstance(e, ParseError):
                sys.stderr.write("{0}\n".format(e))
            # Terminate execution unless we were told not to.
            if exit:
                if isinstance(e, Failure):
                    code = e.result.exited
                elif isinstance(e, Exit):
                    code = e.code
                elif isinstance(e, ParseError):
                    code = 1
                sys.exit(code)
            else:
                debug("Invoked as run(..., exit=False), ignoring exception")

    def normalize_argv(self, argv):
        """
        Massages ``argv`` into a useful list of strings.

        **If None** (the default), uses `sys.argv`.

        **If a non-string iterable**, uses that in place of `sys.argv`.

        **If a string**, performs a `str.split` and then executes with the
        result. (This is mostly a convenience; when in doubt, use a list.)

        Sets ``self.argv`` to the result.
        """
        if argv is None:
            argv = sys.argv
            debug("argv was None; using sys.argv: {0!r}".format(argv))
        elif isinstance(argv, six.string_types):
            argv = argv.split()
            debug("argv was string-like; splitting: {0!r}".format(argv))
        self.argv = argv

    @property
    def name(self):
        """
        Derive program's human-readable name based on init args & argv.
        """
        return self._name or self.argv[0].capitalize()

    @property
    def binary(self):
        """
        Derive program's help-oriented binary name(s) from init args & argv.
        """
        return self._binary or os.path.basename(self.argv[0])

    @property
    def args(self):
        """
        Obtain core program args from ``self.core`` parse result.
        """
        return self.core[0].args

    @property
    def initial_context(self):
        """
        The initial parser context, aka core program flags.

        The specific arguments contained therein will differ depending on
        whether a bundled namespace was specified in `.__init__`.
        """
        args = list(Program.core_args)
        if self.namespace is None:
            args += list(Program.task_args)
        return ParserContext(args=args)

    def print_version(self):
        print("{0} {1}".format(self.name, self.version or "unknown"))

    def print_help(self):
        print("Usage: {0} [--core-opts] task1 [--task1-opts] ... taskN [--taskN-opts]".format(self.binary)) # noqa
        print("")
        print("Core options:")
        print_columns(self.initial_context.help_tuples())

    def parse_core_args(self):
        """
        Filter out core args, leaving any tasks or their args for later.

        Sets ``self.core`` to the `.ParseResult` from this step.
        """
        debug("Parsing initial context (core args)")
        parser = Parser(initial=self.initial_context, ignore_unknown=True)
        self.core = parser.parse_argv(self.argv[1:])
        msg = "Core-args parse result: {0!r} & unparsed: {1!r}"
        debug(msg.format(self.core, self.core.unparsed))

    def load_collection(self):
        """
        Load a task collection based on parsed core args, or die trying.
        """
        start = self.args.root.value
        loader = FilesystemLoader(start=start)
        coll_name = self.args.collection.value
        try:
            coll = loader.load(coll_name) if coll_name else loader.load()
            self.collection = coll
        except CollectionNotFound:
            # TODO: improve sys.exit mocking in tests so we can just raise
            # Exit(msg)
            name = coll_name or DEFAULT_COLLECTION_NAME
            six.print_(
                "Can't find any collection named {0!r}!".format(name),
                file=sys.stderr
            )
            raise Exit(1)

    def parse_tasks(self):
        """
        Parse leftover args, which are typically tasks & per-task args.

        Sets ``self.parser`` to the parser used, and ``self.tasks`` to the
        parse result.
        """
        self.parser = Parser(contexts=self.collection.to_contexts())
        debug("Parsing tasks against {0!r}".format(self.collection))
        self.tasks = self.parser.parse_argv(self.core.unparsed)
        debug("Resulting task contexts: {0!r}".format(self.tasks))

    def print_task_help(self):
        """
        Print help for a specific task, e.g. ``inv --help <taskname>``.
        """
        # Use the parser's contexts dict as that's the easiest way to obtain
        # Context objects here - which are what help output needs.
        name = self.args.help.value
        # Setup
        ctx = self.parser.contexts[name]
        tuples = ctx.help_tuples()
        docstring = inspect.getdoc(self.collection[name])
        header = "Usage: {1} [--core-opts] {0} {{0}}[other tasks here ...]".format(name, self.binary) # noqa
        print(header.format("[--options] " if tuples else ""))
        print("")
        print("Docstring:")
        if docstring:
            # Really wish textwrap worked better for this.
            for line in docstring.splitlines():
                if line.strip():
                    print(indent + line)
                else:
                    print("")
            print("")
        else:
            print(indent + "none")
            print("")
        print("Options:")
        if tuples:
            print_columns(tuples)
        else:
            print(indent + "none")
            print("")

    def list_tasks(self):
        # Sort in depth, then alpha, order
        task_names = self.collection.task_names
        # Short circuit if no tasks to show
        if not task_names:
            msg = "No tasks found in collection '{0}'!"
            print(msg.format(self.collection.name))
            raise Exit
        pairs = []
        for primary in sort_names(task_names):
            # Add aliases
            aliases = sort_names(task_names[primary])
            name = primary
            if aliases:
                name += " ({0})".format(', '.join(aliases))
            # Add docstring 1st lines
            task = self.collection[primary]
            help_ = ""
            if task.__doc__:
                help_ = task.__doc__.lstrip().splitlines()[0]
            pairs.append((name, help_))

        # Print
        print("Available tasks:\n")
        print_columns(pairs)
