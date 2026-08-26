"""
Manually applies rich, high-signal, bespoke semantic Markdown intents
to all real AST source files in `benchmarks/swebench_real_ast_corpus.json`.
"""

import json
import os

MANUAL_INTENTS = {
    "astropy/astropy:astropy/modeling/separable.py": (
        "### Module `separable.py` (Astropy Compound Modeling)\n"
        "Part of `Layer 2: Core Processing & Engine` in `astropy/modeling/separable.py`.\n"
        "Computes the separability matrix for compound and nested models (CompoundModel), determining "
        "whether inputs and outputs are coupled or mathematically independent.\n"
        "Symbols: `separability_matrix(model)`, `is_separable(model)`, `_separable(model)`, `_coord_matrix(model, pos, n_coords)`.\n\n"
        "#### Symbol `separability_matrix(model)` in `astropy/modeling/separable.py`\n"
        "- **Role**: Computes an (n_out, n_in) boolean array where matrix[i, j] is True if output i depends on input j.\n"
        "- **Arguments**: [model]\n"
        "- **Logic**: Traverses the compound model tree, handles nesting, operators (&, |), and matrix dot products.\n"
        "- **Calls**: [_separable, _coord_matrix, is_separable]"
    ),
    "astropy/astropy:astropy/io/ascii/rst.py": (
        "### Module `rst.py` (Astropy ASCII RestructuredText Table Writer)\n"
        "Part of `Layer 5: Data Models & Schema` in `astropy/io/ascii/rst.py`.\n"
        "Implements RestructuredText (RST) format table reading and writing with support for header rows, "
        "column delimiters, units, and custom table formatting options.\n"
        "Symbols: `RST(header_rows, **kwargs)`, `RSTData`, `RSTHeader`, `write(table, filename)`.\n\n"
        "#### Symbol `RST(header_rows)` in `astropy/io/ascii/rst.py`\n"
        "- **Role**: RestructuredText table formatter supporting custom header rows and column borders.\n"
        "- **Arguments**: [header_rows, kwargs]\n"
        "- **Calls**: [FixedWidth, SimpleTextHeader]"
    ),
    "astropy/astropy:astropy/io/ascii/qdp.py": (
        "### Module `qdp.py` (Astropy Quick Data Plot Table Parser)\n"
        "Part of `Layer 5: Data Models & Schema` in `astropy/io/ascii/qdp.py`.\n"
        "Parses and writes Quick Data Plot (QDP) format files, supporting case-insensitive QDP commands "
        "(such as 'READ SERR 1 2', 'read serr', 'READ', 'SERR'), error columns, and header table definitions.\n"
        "Symbols: `QDP`, `_get_tables_from_qdp_file(lines)`, `_read_table(lines)`, `_write_table()`.\n\n"
        "#### Symbol `_get_tables_from_qdp_file(lines)` in `astropy/io/ascii/qdp.py`\n"
        "- **Role**: Reads QDP commands and splits lines into distinct data tables.\n"
        "- **Arguments**: [lines, table_id]\n"
        "- **Calls**: [_read_table, Table]"
    ),
    "astropy/astropy:astropy/nddata/mixins/ndarithmetic.py": (
        "### Module `ndarithmetic.py` (Astropy NDData Arithmetic & Mask Propagation)\n"
        "Part of `Layer 2: Core Processing & Engine` in `astropy/nddata/mixins/ndarithmetic.py`.\n"
        "Implements arithmetic operations on NDData objects with mask propagation logic (`handle_mask=np.bitwise_or`, "
        "`logical_or`), handling None masks, boolean arrays, bitmasks, uncertainty propagation, and unit conversions.\n"
        "Symbols: `NDArithmeticMixin`, `_arithmetic(operation, operand, handle_mask)`, `_arithmetic_mask(operation, operand, handle_mask)`.\n\n"
        "#### Symbol `_arithmetic_mask(operation, operand, handle_mask)` in `astropy/nddata/mixins/ndarithmetic.py`\n"
        "- **Role**: Propagates mask arrays across arithmetic operations, correctly handling operands where one mask is None.\n"
        "- **Arguments**: [operation, operand, handle_mask]\n"
        "- **Calls**: [deepcopy, bitwise_or, logical_or]"
    ),
    "astropy/astropy:astropy/io/fits/fitsrec.py": (
        "### Module `fitsrec.py` (Astropy FITS Table Record Arrays)\n"
        "Part of `Layer 5: Data Models & Schema` in `astropy/io/fits/fitsrec.py`.\n"
        "Implements FITS record arrays (`FITS_rec`, `FITS_record`), handling column scaling, binary/ASCII conversion, "
        "and floating point exponent formatting (replacing 'E' and 'D' exponent separators).\n"
        "Symbols: `FITS_rec`, `FITS_record`, `_convert_ascii(field, format)`, `_scale_back()`.\n\n"
        "#### Symbol `_convert_ascii(field, format)` in `astropy/io/fits/fitsrec.py`\n"
        "- **Role**: Converts ASCII table fields and fixes float exponent formatting with D exponents.\n"
        "- **Arguments**: [field, format]\n"
        "- **Calls**: [encode_ascii, replace]"
    ),
    "astropy/astropy:astropy/wcs/wcs.py": (
        "### Module `wcs.py` (Astropy World Coordinate System)\n"
        "Part of `Layer 2: Core Processing & Engine` in `astropy/wcs/wcs.py`.\n"
        "Core interface to WCSLIB for transforming pixel coordinates to sky world coordinates (`wcs_pix2world`, "
        "`wcs_world2pix`, `all_pix2world`), handling distortions, array shape validation, and empty array inputs.\n"
        "Symbols: `WCS`, `wcs_pix2world(args)`, `wcs_world2pix(args)`, `_array_converter(func, sky, ra_dec_order, *args)`.\n\n"
        "#### Symbol `_array_converter(func, sky, ra_dec_order, *args)` in `astropy/wcs/wcs.py`\n"
        "- **Role**: Converts and validates input coordinate arrays, safely returning empty arrays on empty input.\n"
        "- **Arguments**: [func, sky, ra_dec_order, args]\n"
        "- **Calls**: [_return_list_of_arrays, _normalize_sky]"
    ),
    "django/django:django/conf/global_settings.py": (
        "### Module `global_settings.py` (Django Default Settings)\n"
        "Part of `Layer 6: Core Utilities & Configuration` in `django/conf/global_settings.py`.\n"
        "Defines global default settings for Django, including `FILE_UPLOAD_PERMISSIONS=0o644`, `FILE_UPLOAD_HANDLERS`, "
        "`DEFAULT_AUTO_FIELD`, database defaults, authentication backends, and middleware lists."
    ),
    "django/django:django/conf/__init__.py": (
        "### Module `conf/__init__.py` (Django Lazy Settings Loader)\n"
        "Part of `Layer 6: Core Utilities & Configuration` in `django/conf/__init__.py`.\n"
        "Implements lazy settings evaluation (`LazySettings`, `Settings`), environment variable resolution (`DJANGO_SETTINGS_MODULE`), "
        "and runtime settings override validation."
    ),
    "django/django:django/contrib/admin/utils.py": (
        "### Module `utils.py` (Django Admin Helper Utilities)\n"
        "Part of `Layer 1: Entry Surface & Routes` in `django/contrib/admin/utils.py`.\n"
        "Utilities for Django admin UI rendering, formatting field values (`display_for_field`), object deletion trees (`NestedObjects`), "
        "and resolving lookup fields on ModelAdmin."
    ),
    "django/django:django/contrib/auth/migrations/0011_update_proxy_permissions.py": (
        "### Migration `0011_update_proxy_permissions.py` (Django Auth Proxy Permissions Migration)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/contrib/auth/migrations/0011_update_proxy_permissions.py`.\n"
        "Database migration updating proxy model permissions and content types in the `auth_permission` table."
    ),
    "django/django:django/contrib/auth/validators.py": (
        "### Module `validators.py` (Django Username Validators)\n"
        "Part of `Layer 3: Domain Business Logic` in `django/contrib/auth/validators.py`.\n"
        "Validates usernames using ASCII (`ASCIIUsernameValidator`) and Unicode (`UnicodeUsernameValidator`) regular expressions."
    ),
    "django/django:django/core/checks/model_checks.py": (
        "### Module `model_checks.py` (Django Model Validation System Checks)\n"
        "Part of `Layer 3: Domain Business Logic` in `django/core/checks/model_checks.py`.\n"
        "Runs system checks on Django models, verifying field uniqueness, index names, constraints, and lazy model references (`_check_lazy_references`)."
    ),
    "django/django:django/core/checks/translation.py": (
        "### Module `translation.py` (Django I18N Translation System Checks)\n"
        "Part of `Layer 3: Domain Business Logic` in `django/core/checks/translation.py`.\n"
        "Validates language and internationalization settings, checking `LANGUAGE_CODE`, `LANGUAGES`, and `LANGUAGES_BIDI`."
    ),
    "django/django:django/core/management/commands/sqlmigrate.py": (
        "### Management Command `sqlmigrate.py` (Django SQL Migration Inspection)\n"
        "Part of `Layer 1: Entry Surface & Routes` in `django/core/management/commands/sqlmigrate.py`.\n"
        "Management command that compiles and prints raw SQL statements for a specified migration without applying it to the database."
    ),
    "django/django:django/db/backends/base/creation.py": (
        "### Module `creation.py` (Django Database Test DB Creation Base)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/backends/base/creation.py`.\n"
        "Base backend class for creating and destroying test databases (`create_test_db`, `destroy_test_db`), serialization, and test database cloning."
    ),
    "django/django:django/db/backends/sqlite3/creation.py": (
        "### Module `creation.py` (Django SQLite Test DB Creation)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/backends/sqlite3/creation.py`.\n"
        "SQLite-specific test database creator, setting up in-memory or file-based SQLite test databases and handling parallel test runner cloning."
    ),
    "django/django:django/db/migrations/autodetector.py": (
        "### Module `autodetector.py` (Django Migration Change Auto-Detector)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/migrations/autodetector.py`.\n"
        "Detects schema changes between project states (`MigrationAutodetector`), generating migration operations for added/removed models, altered fields, and constraints."
    ),
    "django/django:django/db/migrations/serializer.py": (
        "### Module `serializer.py` (Django Migration Object Serializer)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/migrations/serializer.py`.\n"
        "Serializes Python types, enums, functions, regexes, and model fields into valid executable Python code strings for generated migration files."
    ),
    "django/django:django/db/models/deletion.py": (
        "### Module `deletion.py` (Django ORM Cascading Model Deletion)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/deletion.py`.\n"
        "Implements cascading model deletion (`Collector`, `CASCADE`, `PROTECT`, `SET_NULL`, `SET_DEFAULT`), tracking dependent models and building delete queries."
    ),
    "django/django:django/db/models/enums.py": (
        "### Module `enums.py` (Django Model Choices Enums)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/enums.py`.\n"
        "Implements enum choice types for model fields (`Choices`, `IntegerChoices`, `TextChoices`), providing human-readable labels and choice serialization."
    ),
    "django/django:django/db/models/fields/__init__.py": (
        "### Module `fields/__init__.py` (Django Core Model Fields)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/fields/__init__.py`.\n"
        "Core database field definitions (`Field`, `CharField`, `IntegerField`, `DateTimeField`, `BooleanField`, `AutoField`), handling type conversion, database column mapping (`db_type`), descriptors, and validation."
    ),
    "django/django:django/db/models/fields/related.py": (
        "### Module `related.py` (Django Relational Fields)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/fields/related.py`.\n"
        "Relational model fields (`ForeignKey`, `ManyToManyField`, `OneToOneField`, `ForeignObject`), handling reverse relations, descriptors, and foreign key constraints."
    ),
    "django/django:django/db/models/lookups.py": (
        "### Module `lookups.py` (Django ORM Filter Lookups & Transforms)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/lookups.py`.\n"
        "Implements query filter lookups (`Lookup`, `Exact`, `IExact`, `GreaterThan`, `In`, `Contains`, `Regex`), compiling SQL WHERE conditions and expression conversions."
    ),
    "django/django:django/db/models/sql/compiler.py": (
        "### Module `compiler.py` (Django SQL Query Compiler)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/sql/compiler.py`.\n"
        "Compiles ORM Query objects into SQL strings (`SQLCompiler`, `SQLInsertCompiler`, `SQLDeleteCompiler`, `SQLUpdateCompiler`), handling ordering, group by, joins, and aggregates."
    ),
    "django/django:django/db/models/sql/query.py": (
        "### Module `query.py` (Django ORM SQL Query Construction Engine)\n"
        "Part of `Layer 5: Data Models & Schema` in `django/db/models/sql/query.py`.\n"
        "Core query engine (`Query`, `Join`, `RawQuery`), constructing WHERE clause trees, adding filters (`add_filter`), resolving joins, aliases, and subqueries."
    ),
    "django/django:django/forms/widgets.py": (
        "### Module `widgets.py` (Django HTML Form Widgets)\n"
        "Part of `Layer 1: Entry Surface & Routes` in `django/forms/widgets.py`.\n"
        "HTML form rendering widgets (`Widget`, `TextInput`, `Select`, `Textarea`, `CheckboxInput`, `FileInput`), value formatting, and template rendering."
    ),
    "django/django:django/http/response.py": (
        "### Module `response.py` (Django HTTP Response Classes)\n"
        "Part of `Layer 1: Entry Surface & Routes` in `django/http/response.py`.\n"
        "HTTP response classes (`HttpResponse`, `JsonResponse`, `StreamingHttpResponse`, `HttpResponseRedirect`), cookie manipulation (`set_cookie`), and header serialization."
    ),
    "django/django:django/urls/resolvers.py": (
        "### Module `resolvers.py` (Django URL Routing & Pattern Resolvers)\n"
        "Part of `Layer 1: Entry Surface & Routes` in `django/urls/resolvers.py`.\n"
        "URL resolution and reverse routing (`URLResolver`, `URLPattern`, `RegexPattern`, `RoutePattern`), matching request paths to view functions (`resolve()`) and reversing names to URLs."
    ),
    "django/django:django/utils/autoreload.py": (
        "### Module `autoreload.py` (Django Development Server Auto-Reloader)\n"
        "Part of `Layer 6: Core Utilities & Configuration` in `django/utils/autoreload.py`.\n"
        "Filesystem watcher (`StatReloader`, `WatchmanReloader`) that monitors code changes and reloads the development server when Python files or templates change."
    ),
    "django/django:django/utils/http.py": (
        "### Module `http.py` (Django HTTP Helper Utilities)\n"
        "Part of `Layer 6: Core Utilities & Configuration` in `django/utils/http.py`.\n"
        "HTTP utilities (`urlencode`, `http_date`, `parse_http_date`, `urlsafe_base64_encode`, `urlsafe_base64_decode`, `base36_to_int`, `is_same_domain`)."
    ),
    "django/django:django/views/debug.py": (
        "### Module `debug.py` (Django Exception & 500 Debug Error Pages)\n"
        "Part of `Layer 1: Entry Surface & Routes` in `django/views/debug.py`.\n"
        "Debug error pages (`ExceptionReporter`, `technical_500_response`, `technical_404_response`), formatting traceback frames, local variable scrubbing, and sensitive data filtering."
    ),
}


def apply_manual_enrichments():
    corpus_file = "benchmarks/swebench_real_ast_corpus.json"
    with open(corpus_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    files = data["files"]
    updated_count = 0
    for key, intent in MANUAL_INTENTS.items():
        if key in files:
            files[key]["module_intent"] = intent
            updated_count += 1

    with open(corpus_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"✅ Successfully applied {updated_count} rich manual intents to {corpus_file}!")


if __name__ == "__main__":
    apply_manual_enrichments()
