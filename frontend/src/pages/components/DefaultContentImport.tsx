import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { createPortal } from "react-dom";
import {
    FaChevronRight,
    FaFolder,
    FaFolderOpen,
    FaFileAlt,
    FaTimes,
    FaDownload,
    FaPlusCircle,
} from "react-icons/fa";
import "../../styling/DefaultContentImport.scss";

type Item = {
    key: string;
    module: string;
    label: string;
    imported: boolean;
    error: string | null;
};

type Catalog = {
    enabled: boolean;
    items: Item[];
};

const availableKeys = (data: Catalog) =>
    data.enabled
        ? data.items
            .filter((item) => !item.imported && !item.error)
            .map((item) => item.key)
        : [];

const headers = () => ({
    Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
});

const endpoint = () =>
    `${import.meta.env.VITE_API_URL}/projects/default_content`;

const message = (error: unknown) =>
    axios.isAxiosError(error)
        ? error.response?.data?.message || error.message
        : "Could not load default projects.";

export default function DefaultContentImport({
    classId,
    onImported,
    onCreateCustom,
    onToggleCreate,
    createOpen = false,
}: {
    classId: string;
    onImported: () => void;
    onCreateCustom?: () => void;
    onToggleCreate?: () => void;
    createOpen?: boolean;
}) {
    const dialog = useRef<HTMLDialogElement>(null);
    const [catalog, setCatalog] = useState<Catalog>({
        enabled: false,
        items: [],
    });
    const [selected, setSelected] = useState<string[]>([]);
    const [busy, setBusy] = useState(false);
    const [fetching, setFetching] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");

    const [open, setOpen] = useState(false);
    const [expanded, setExpanded] = useState<string[]>([]);

    const groups = useMemo(() => {
        const result = new Map<string, Item[]>();

        catalog.items.forEach((item) =>
            result.set(item.module, [
                ...(result.get(item.module) || []),
                item,
            ]),
        );

        return Array.from(result.entries());
    }, [catalog.items]);

    const openDialog = () => {
        setExpanded([]);

        if (!dialog.current?.open) {
            dialog.current?.showModal();
        }

        setOpen(true);
    };

    useEffect(() => {
        if (!open) return;

        const body = document.body;
        const html = document.documentElement;
        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        const properties = [
            "position",
            "top",
            "left",
            "width",
            "overflow",
            "padding-right",
        ];

        const previous = properties.map((name) => [
            name,
            body.style.getPropertyValue(name),
            body.style.getPropertyPriority(name),
        ]);

        const htmlOverflow = html.style.getPropertyValue("overflow");
        const htmlPriority = html.style.getPropertyPriority("overflow");

        const gap = window.innerWidth - html.clientWidth;
        const padding =
            parseFloat(getComputedStyle(body).paddingRight) || 0;

        body.style.setProperty("position", "fixed");
        body.style.setProperty("top", `-${scrollY}px`);
        body.style.setProperty("left", `-${scrollX}px`);
        body.style.setProperty("width", "100%");
        body.style.setProperty("overflow", "hidden");
        body.style.setProperty(
            "padding-right",
            `${padding + gap}px`,
        );
        html.style.setProperty("overflow", "hidden");

        return () => {
            previous.forEach(([name, value, priority]) => {
                if (value) {
                    body.style.setProperty(name, value, priority);
                } else {
                    body.style.removeProperty(name);
                }
            });

            if (htmlOverflow) {
                html.style.setProperty(
                    "overflow",
                    htmlOverflow,
                    htmlPriority,
                );
            } else {
                html.style.removeProperty("overflow");
            }

            const behavior =
                html.style.getPropertyValue("scroll-behavior");
            const priority =
                html.style.getPropertyPriority("scroll-behavior");

            html.style.setProperty(
                "scroll-behavior",
                "auto",
                "important",
            );

            window.scrollTo(scrollX, scrollY);

            if (behavior) {
                html.style.setProperty(
                    "scroll-behavior",
                    behavior,
                    priority,
                );
            } else {
                html.style.removeProperty("scroll-behavior");
            }
        };
    }, [open]);

    useEffect(() => {
        const controller = new AbortController();

        setFetching(true);

        axios
            .get<Catalog>(endpoint(), {
                headers: headers(),
                params: { class_id: classId },
                signal: controller.signal,
            })
            .then(({ data }) => {
                setCatalog(data);
                setSelected(availableKeys(data));

                if (
                    data.enabled &&
                    data.items.some((item) => !item.imported)
                ) {
                    openDialog();
                }
            })
            .catch((error) => {
                if (!controller.signal.aborted) {
                    setError(message(error));
                }
            })
            .finally(() => {
                if (!controller.signal.aborted) {
                    setFetching(false);
                }
            });

        return () => controller.abort();
    }, [classId]);

    const refresh = async (selectAll = true) => {
        setFetching(true);
        setError("");

        try {
            const { data } = await axios.get<Catalog>(endpoint(), {
                headers: headers(),
                params: { class_id: classId },
            });

            setCatalog(data);

            const available = availableKeys(data);

            setSelected((current) =>
                selectAll
                    ? available
                    : current.filter((key) =>
                        available.includes(key),
                    ),
            );

            return data;
        } catch (error) {
            setError(message(error));
            return null;
        } finally {
            setFetching(false);
        }
    };

    const importSelected = async () => {
        if (busy || fetching || !selected.length) return;

        setBusy(true);
        setError("");
        setNotice("");

        try {
            const { data } = await axios.post(
                endpoint(),
                {
                    class_id: classId,
                    selected,
                },
                {
                    headers: headers(),
                },
            );

            setNotice(`Imported ${data.imported} project(s).`);
            setSelected([]);
            onImported();

            await refresh(false);
        } catch (error) {
            setError(
                `${message(error)} Refresh the list before retrying.`,
            );
            setSelected([]);
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            {catalog.enabled || error ? (
                <button
                    type="button"
                    className="default-import-trigger"
                    disabled={busy || fetching}
                    onClick={() => {
                        openDialog();
                        void refresh();
                    }}
                >
                    <FaDownload aria-hidden="true" />
                    {onCreateCustom
                        ? "Import / create module"
                        : "Import default projects"}
                </button>
            ) : (
                onCreateCustom && (
                    <button
                        type="button"
                        className="button button-create-assignment"
                        disabled={fetching}
                        onClick={
                            onToggleCreate || onCreateCustom
                        }
                    >
                        <FaPlusCircle aria-hidden="true" />
                        <span className="button-text">
                            {createOpen
                                ? "Close create module"
                                : "Create new module"}
                        </span>
                    </button>
                )
            )}

            {fetching && !open && (
                <span role="status">
                    Loading default projects…
                </span>
            )}

            {error && !open && <p role="alert">{error}</p>}

            {createPortal(
                <dialog
                    ref={dialog}
                    className="default-import-dialog"
                    aria-labelledby="default-import-title"
                    aria-describedby="default-import-description"
                    aria-busy={busy || fetching}
                    onClose={() => setOpen(false)}
                    onCancel={(event) => {
                        if (busy) {
                            event.preventDefault();
                        }
                    }}
                >
                    <header className="default-import-header">
                        <div>
                            <span className="default-import-eyebrow">
                                ASSIGNMENT CONTENT
                            </span>
                            <h2 id="default-import-title">
                                Import default projects
                            </h2>
                        </div>

                        <button
                            className="default-import-icon-button"
                            type="button"
                            aria-label="Close import dialog"
                            disabled={busy}
                            onClick={() =>
                                dialog.current?.close()
                            }
                        >
                            <FaTimes aria-hidden="true" />
                        </button>
                    </header>

                    <p
                        id="default-import-description"
                        className="default-import-description"
                    >
                        All available assignments start selected.
                        Select a whole module or open it to choose
                        individual assignments.
                    </p>

                    <div className="default-import-content">
                        {error && (
                            <p
                                className="default-import-alert"
                                role="alert"
                            >
                                {error}
                            </p>
                        )}

                        {notice && (
                            <p
                                className="default-import-success"
                                role="status"
                            >
                                {notice}
                            </p>
                        )}

                        {(busy || fetching) && (
                            <div
                                className="default-import-progress"
                                role="status"
                                aria-live="polite"
                            >
                                <span
                                    className="default-import-spinner"
                                    aria-hidden="true"
                                />
                                {busy
                                    ? "Importing projects. Please keep this window open…"
                                    : "Loading projects…"}
                            </div>
                        )}

                        {!catalog.enabled && !fetching && (
                            <p className="default-import-empty">
                                Default projects are not enabled for
                                this school.
                            </p>
                        )}

                        {catalog.enabled &&
                            !catalog.items.length &&
                            !fetching && (
                                <p className="default-import-empty">
                                    No default projects are available.
                                </p>
                            )}

                        <div className="default-import-folders">
                            {groups.map(
                                ([module, items], index) => {
                                    const isExpanded =
                                        expanded.includes(module);

                                    const imported = items.filter(
                                        (item) => item.imported,
                                    ).length;

                                    const count = items.filter(
                                        (item) =>
                                            selected.includes(
                                                item.key,
                                            ),
                                    ).length;

                                    const available = items.filter(
                                        (item) =>
                                            !item.imported &&
                                            !item.error,
                                    );

                                    const allSelected =
                                        available.length > 0 &&
                                        available.every((item) =>
                                            selected.includes(
                                                item.key,
                                            ),
                                        );

                                    const someSelected =
                                        available.some((item) =>
                                            selected.includes(
                                                item.key,
                                            ),
                                        );

                                    const panelId =
                                        `default-import-module-${index}`;

                                    return (
                                        <section
                                            className="default-import-folder"
                                            key={module}
                                        >
                                            <div className="default-import-folder-header">
                                                <input
                                                    type="checkbox"
                                                    className="default-import-module-checkbox"
                                                    aria-label={`Select all available assignments in ${module}`}
                                                    checked={
                                                        allSelected ||
                                                        imported ===
                                                        items.length
                                                    }
                                                    ref={(
                                                        element,
                                                    ) => {
                                                        if (
                                                            element
                                                        ) {
                                                            element.indeterminate =
                                                                someSelected &&
                                                                !allSelected;
                                                        }
                                                    }}
                                                    disabled={
                                                        busy ||
                                                        fetching ||
                                                        !available.length
                                                    }
                                                    onChange={(
                                                        event,
                                                    ) => {
                                                        const checked =
                                                            event
                                                                .target
                                                                .checked;

                                                        const keys =
                                                            available.map(
                                                                (
                                                                    item,
                                                                ) =>
                                                                    item.key,
                                                            );

                                                        setSelected(
                                                            (
                                                                current,
                                                            ) =>
                                                                checked
                                                                    ? Array.from(
                                                                        new Set(
                                                                            [
                                                                                ...current,
                                                                                ...keys,
                                                                            ],
                                                                        ),
                                                                    )
                                                                    : current.filter(
                                                                        (
                                                                            key,
                                                                        ) =>
                                                                            !keys.includes(
                                                                                key,
                                                                            ),
                                                                    ),
                                                        );
                                                    }}
                                                />

                                                <button
                                                    type="button"
                                                    className="default-import-folder-toggle"
                                                    aria-expanded={
                                                        isExpanded
                                                    }
                                                    aria-controls={
                                                        panelId
                                                    }
                                                    onClick={() =>
                                                        setExpanded(
                                                            (
                                                                current,
                                                            ) =>
                                                                isExpanded
                                                                    ? current.filter(
                                                                        (
                                                                            name,
                                                                        ) =>
                                                                            name !==
                                                                            module,
                                                                    )
                                                                    : [
                                                                        ...current,
                                                                        module,
                                                                    ],
                                                        )
                                                    }
                                                >
                                                    <FaChevronRight
                                                        className="default-import-chevron"
                                                        aria-hidden="true"
                                                    />

                                                    {isExpanded ? (
                                                        <FaFolderOpen
                                                            className="default-import-folder-icon"
                                                            aria-hidden="true"
                                                        />
                                                    ) : (
                                                        <FaFolder
                                                            className="default-import-folder-icon"
                                                            aria-hidden="true"
                                                        />
                                                    )}

                                                    <span className="default-import-folder-name">
                                                        {module}
                                                        <small>
                                                            {
                                                                items.length
                                                            }{" "}
                                                            assignment
                                                            {items.length ===
                                                                1
                                                                ? ""
                                                                : "s"}{" "}
                                                            ·{" "}
                                                            {
                                                                imported
                                                            }{" "}
                                                            imported
                                                        </small>
                                                    </span>

                                                    {count > 0 && (
                                                        <span className="default-import-count">
                                                            {
                                                                count
                                                            }{" "}
                                                            selected
                                                        </span>
                                                    )}
                                                </button>
                                            </div>

                                            <div
                                                id={panelId}
                                                hidden={
                                                    !isExpanded
                                                }
                                                className="default-import-assignments"
                                            >
                                                {items.map(
                                                    (item) => (
                                                        <div
                                                            key={
                                                                item.key
                                                            }
                                                            className="default-import-assignment"
                                                        >
                                                            <label>
                                                                <input
                                                                    type="checkbox"
                                                                    checked={
                                                                        item.imported ||
                                                                        selected.includes(
                                                                            item.key,
                                                                        )
                                                                    }
                                                                    disabled={
                                                                        busy ||
                                                                        fetching ||
                                                                        item.imported ||
                                                                        !!item.error
                                                                    }
                                                                    onChange={(
                                                                        event,
                                                                    ) =>
                                                                        setSelected(
                                                                            (
                                                                                current,
                                                                            ) =>
                                                                                event
                                                                                    .target
                                                                                    .checked
                                                                                    ? [
                                                                                        ...current,
                                                                                        item.key,
                                                                                    ]
                                                                                    : current.filter(
                                                                                        (
                                                                                            key,
                                                                                        ) =>
                                                                                            key !==
                                                                                            item.key,
                                                                                    ),
                                                                        )
                                                                    }
                                                                />

                                                                <FaFileAlt aria-hidden="true" />

                                                                <span className="default-import-assignment-name">
                                                                    {
                                                                        item.label
                                                                    }
                                                                </span>

                                                                {item.imported && (
                                                                    <span className="default-import-badge">
                                                                        Imported
                                                                    </span>
                                                                )}

                                                                {item.error && (
                                                                    <span className="default-import-badge default-import-badge-error">
                                                                        Unavailable
                                                                    </span>
                                                                )}
                                                            </label>

                                                            {item.error && (
                                                                <p className="default-import-item-error">
                                                                    {
                                                                        item.error
                                                                    }
                                                                </p>
                                                            )}
                                                        </div>
                                                    ),
                                                )}
                                            </div>
                                        </section>
                                    );
                                },
                            )}
                        </div>

                        {onCreateCustom && (
                            <div className="default-import-actions">
                                <button
                                    type="button"
                                    disabled={busy}
                                    onClick={() => {
                                        dialog.current?.close();
                                        setOpen(false);
                                        onCreateCustom();
                                    }}
                                >
                                    <FaPlusCircle aria-hidden="true" />
                                    Create custom module
                                </button>
                            </div>
                        )}
                    </div>

                    <footer className="default-import-footer">
                        <span>
                            {selected.length} selected{" "}
                            <small>
                                Imported assignments stay in your
                                class.
                            </small>
                        </span>

                        <div className="default-import-actions">
                            <button
                                type="button"
                                disabled={busy || fetching}
                                onClick={() => void refresh()}
                            >
                                Refresh
                            </button>

                            <button
                                type="button"
                                disabled={busy}
                                onClick={() =>
                                    dialog.current?.close()
                                }
                            >
                                Close
                            </button>

                            <button
                                className="default-import-primary"
                                type="button"
                                disabled={
                                    busy ||
                                    fetching ||
                                    !selected.length
                                }
                                onClick={() =>
                                    void importSelected()
                                }
                            >
                                {busy
                                    ? "Importing…"
                                    : "Import selected"}
                            </button>
                        </div>
                    </footer>
                </dialog>,
                document.body,
            )}
        </>
    );
}