# SPDX-License-Identifier: GPL-3.0-or-later

"""A completion category that queries the SQL-backed Roblox API store."""

from typing import Optional

from qutebrowser.qt.sql import QSqlQueryModel
from qutebrowser.qt.widgets import QWidget

from qutebrowser.misc import sql
from qutebrowser.utils import debug, message
from qutebrowser.completion.models import BaseCategory


class RobloxApiCategory(QSqlQueryModel, BaseCategory):

    """A completion category querying a single Roblox API member type.

    Modeled on :class:`histcategory.HistoryCategory`: filtering and sorting
    happen inside SQLite rather than in Python, so it stays fast even with the
    full (~5000 entry) API dump.
    """

    def __init__(self, *, database: sql.Database, name: str, type_: str,
                 parent: QWidget = None) -> None:
        super().__init__(parent=parent)
        self._database = database
        self.name = name
        self._type = type_
        # advertise that this model filters by the name column
        self.columns_to_filter = [0]
        self.delete_func = None
        self._query: Optional[sql.Query] = None
        self._nwords = 0

    def set_pattern(self, pattern: str) -> None:
        """Set the pattern used to filter results.

        Args:
            pattern: string pattern to filter by.
        """
        # escape to treat a user input % or _ as a literal, not a wildcard
        pattern = pattern.replace('%', '\\%')
        pattern = pattern.replace('_', '\\_')
        words = ['%{}%'.format(w) for w in pattern.split(' ')]

        # match all words in any order, e.g. "a b" -> name LIKE %a% AND name LIKE %b%
        where_clause = ' AND '.join(
            "name LIKE :{val} escape '\\'".format(val=i)
            for i in range(len(words)))

        try:
            if self._query is None or self._nwords != len(words):
                # only rebuild the prepared query when the word count changes;
                # otherwise reuse it and just rebind for performance
                self._nwords = len(words)
                self._query = self._database.query(' '.join([
                    "SELECT name, url, description",
                    "FROM RobloxApi",
                    "WHERE type = :type AND ({})".format(where_clause),
                    "ORDER BY name",
                ]), forward_only=False)

            with debug.log_time('sql', 'Running robloxapi completion query'):
                values = {str(i): w for i, w in enumerate(words)}
                self._query.run(type=self._type, **values)
        except sql.KnownError as e:
            message.error("Error with SQL query: {}".format(e.text()))
            return

        self.setQuery(self._query.query)
