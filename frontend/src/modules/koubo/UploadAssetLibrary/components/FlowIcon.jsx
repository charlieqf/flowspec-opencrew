import addUrl from "../assets/icons/material-symbols/add.svg";
import addNotesUrl from "../assets/icons/material-symbols/add-notes.svg";
import arrowBackUrl from "../assets/icons/material-symbols/arrow-back.svg";
import arrowForwardUrl from "../assets/icons/material-symbols/arrow-forward.svg";
import closeUrl from "../assets/icons/material-symbols/close.svg";
import deleteUrl from "../assets/icons/material-symbols/delete-outline.svg";
import downloadUrl from "../assets/icons/material-symbols/download.svg";
import editSquareUrl from "../assets/icons/material-symbols/edit-square-outline.svg";
import favoriteUrl from "../assets/icons/material-symbols/favorite.svg";
import flagUrl from "../assets/icons/material-symbols/flag.svg";
import imageUrl from "../assets/icons/material-symbols/image-outline.svg";
import leftPanelCloseUrl from "../assets/icons/material-symbols/left-panel-close.svg";
import menuUrl from "../assets/icons/material-symbols/menu.svg";
import moreVertUrl from "../assets/icons/material-symbols/more-vert.svg";
import radioButtonUncheckedUrl from "../assets/icons/material-symbols/radio-button-unchecked.svg";
import redoUrl from "../assets/icons/material-symbols/redo.svg";
import searchUrl from "../assets/icons/material-symbols/search.svg";
import setCoverUrl from "../assets/icons/material-symbols/set-cover.svg";
import shareUrl from "../assets/icons/material-symbols/share.svg";
import swapUrl from "../assets/icons/material-symbols/swap.svg";
import tuneUrl from "../assets/icons/material-symbols/tune.svg";

const iconUrls = {
  add: addUrl,
  addNotes: addNotesUrl,
  arrowBack: arrowBackUrl,
  arrowForward: arrowForwardUrl,
  close: closeUrl,
  delete: deleteUrl,
  download: downloadUrl,
  editSquare: editSquareUrl,
  favorite: favoriteUrl,
  flag: flagUrl,
  image: imageUrl,
  leftPanelClose: leftPanelCloseUrl,
  menu: menuUrl,
  moreVert: moreVertUrl,
  radioButtonUnchecked: radioButtonUncheckedUrl,
  redo: redoUrl,
  search: searchUrl,
  setCover: setCoverUrl,
  share: shareUrl,
  swap: swapUrl,
  tune: tuneUrl,
};

const paths = {
  add: "M11 13H5v-2h6V5h2v6h6v2h-6v6h-2z",
  addNotes: "M17.5 21h1v-2.5H21v-1h-2.5V15h-1v2.5H15v1h2.5zm.5 2q-2.075 0-3.537-1.463T13 18t1.463-3.537T18 13t3.538 1.463T23 18t-1.463 3.538T18 23M7 9h10V7H7zm4.675 12H5q-.825 0-1.412-.587T3 19V5q0-.825.588-1.412T5 3h14q.825 0 1.413.588T21 5v6.7q-.725-.35-1.463-.525T18 11q-.275 0-.513.012t-.487.063V11H7v2h6.125q-.45.425-.812.925T11.675 15H7v2h4.075q-.05.25-.062.488T11 18q0 .825.15 1.538T11.675 21",
  arrowBack: "m7.825 13l5.6 5.6L12 20l-8-8l8-8l1.425 1.4l-5.6 5.6H20v2z",
  arrowForward: "M16.175 13H4v-2h12.175l-5.6-5.6L12 4l8 8l-8 8l-1.425-1.4z",
  check: "m9.55 17.3l-5.3-5.3l1.4-1.4l3.9 3.9l8.8-8.8l1.4 1.4z",
  close: "M6.4 19L5 17.6l5.6-5.6L5 6.4L6.4 5l5.6 5.6L17.6 5L19 6.4L13.4 12l5.6 5.6l-1.4 1.4l-5.6-5.6z",
  contentCopy: "M7 18q-.825 0-1.412-.587T5 16V4q0-.825.588-1.412T7 2h9q.825 0 1.413.588T18 4v12q0 .825-.587 1.413T16 18zm0-2h9V4H7zm-4 6q-.825 0-1.412-.587T1 20V7h2v13h10v2z",
  delete: "M7 21q-.825 0-1.412-.587T5 19V6H4V4h5V3h6v1h5v2h-1v13q0 .825-.587 1.413T17 21zM17 6H7v13h10zM9 17h2V8H9zm4 0h2V8h-2zM7 6v13z",
  download: "M12 16l-5-5l1.4-1.45l2.6 2.6V4h2v8.15l2.6-2.6L17 11zm-6 4q-.825 0-1.412-.587T4 18v-3h2v3h12v-3h2v3q0 .825-.587 1.413T18 20z",
  editSquare: "M5 21q-.825 0-1.412-.587T3 19V5q0-.825.588-1.412T5 3h8.925l-2 2H5v14h14v-6.95l2-2V19q0 .825-.587 1.413T19 21zm4-6v-4.25l9.175-9.175q.3-.3.675-.45t.75-.15q.4 0 .763.15t.662.45L22.425 3q.275.3.425.663T23 4.4t-.137.738t-.438.662L13.25 15zM21.025 4.4l-1.4-1.4zM11 13h1.4l5.8-5.8l-.7-.7l-.725-.7L11 11.575zm6.5-6.5l-.725-.7zl.7.7z",
  favorite: "m12 21l-1.45-1.3q-2.525-2.275-4.175-3.925T3.75 12.8T2.388 10.2T2 7.5Q2 5.225 3.525 3.613T7.35 2q1.3 0 2.475.55T12 4.1q1-1 2.175-1.55T16.65 2q2.3 0 3.825 1.613T22 7.5q0 1.4-.388 2.7T20.25 12.8t-2.625 2.975T13.45 19.7zM12 18.3q2.4-2.15 3.95-3.688t2.45-2.675t1.25-2.037T20 7.5q0-1.45-.95-2.475T16.65 4q-1.125 0-2.087.638T13.25 6.3h-2.5q-.35-1.025-1.312-1.663T7.35 4q-1.45 0-2.4 1.025T4 7.5q0 .75.35 1.525t1.25 1.912t2.45 2.675T12 18.3m0-7.15",
  flag: "M5 21V4h10.4l.4 2H21v11h-7.4l-.4-2H7v6zm8.25-6H19V8h-4.85l-.4-2H7v7h7.85z",
  folder: "M4 20q-.825 0-1.412-.587T2 18V6q0-.825.588-1.412T4 4h6l2 2h8q.825 0 1.413.588T22 8v10q0 .825-.587 1.413T20 20zm0-2h16V8h-8.825l-2-2H4zm0 0V6z",
  fullscreen: "M5 19h5v2H3v-7h2zm0-9H3V3h7v2H5zm14 9v-5h2v7h-7v-2zm0-14h-5V3h7v7h-2z",
  gridView: "M3 11V3h8v8zm2-2h4V5H5zm8 2V3h8v8zm2-2h4V5h-4zM3 21v-8h8v8zm2-2h4v-4H5zm8 2v-8h8v8zm2-2h4v-4h-4z",
  history: "M12 21q-3.35 0-5.9-2.025T2.65 13.8l1.95-.45q.7 2.45 2.725 4.05T12 19q2.925 0 4.963-2.037T19 12t-2.037-4.963T12 5q-1.725 0-3.2.8T6.4 8H9v2H3V4h2v2.35q1.275-1.6 3.113-2.475T12 3q3.75 0 6.375 2.625T21 12t-2.625 6.375T12 21m2.8-4.8L11 12.4V7h2v4.6l3.2 3.2z",
  image: "M5 21q-.825 0-1.412-.587T3 19V5q0-.825.588-1.412T5 3h14q.825 0 1.413.588T21 5v14q0 .825-.587 1.413T19 21zm0-2h14V5H5zm1-2h12l-3.75-5l-3 4L9 13zm-1 2V5z",
  audio: "M12 21q-2.075 0-3.537-1.463T7 16t1.463-3.537T12 11q.575 0 1.063.1t.937.3V3h6v4h-4v9q0 2.075-1.463 3.538T12 21m0-2q1.25 0 2.125-.875T15 16t-.875-2.125T12 13t-2.125.875T9 16t.875 2.125T12 19",
  video: "M4 20q-.825 0-1.412-.587T2 18V6q0-.825.588-1.412T4 4h12q.825 0 1.413.588T18 6v4.5l4-4v11l-4-4V18q0 .825-.587 1.413T16 20zm0-2h12V6H4zm0 0V6z",
  leftPanelClose: "M16.5 16V8l-4 4zM5 21q-.825 0-1.412-.587T3 19V5q0-.825.588-1.412T5 3h14q.825 0 1.413.588T21 5v14q0 .825-.587 1.413T19 21zm5-2h9V5h-9z",
  menu: "M3 18v-2h18v2zm0-5v-2h18v2zm0-5V6h18v2z",
  moreVert: "M12 20q-.825 0-1.412-.587T10 18t.588-1.412T12 16t1.413.588T14 18t-.587 1.413T12 20m0-6q-.825 0-1.412-.587T10 12t.588-1.412T12 10t1.413.588T14 12t-.587 1.413T12 14m0-6q-.825 0-1.412-.587T10 6t.588-1.412T12 4t1.413.588T14 6t-.587 1.413T12 8",
  pictureInPicture: "M4 19q-.825 0-1.412-.587T2 17V7q0-.825.588-1.412T4 5h16q.825 0 1.413.588T22 7v10q0 .825-.587 1.413T20 19zm0-2h16V7H4zm10-1h5v-4h-5zM4 17V7z",
  radioButtonUnchecked: "M12 22q-2.075 0-3.9-.788t-3.175-2.137T2.788 15.9T2 12t.788-3.9t2.137-3.175T8.1 2.788T12 2t3.9.788t3.175 2.137T21.213 8.1T22 12t-.788 3.9t-2.137 3.175t-3.175 2.138T12 22m0-2q3.35 0 5.675-2.325T20 12t-2.325-5.675T12 4T6.325 6.325T4 12t2.325 5.675T12 20",
  redo: "M9 20q-2.5 0-4.25-1.75T3 14t1.75-4.25T9 8h7.2l-2.6-2.6L15 4l5 5l-5 5l-1.4-1.4l2.6-2.6H9q-1.65 0-2.825 1.175T5 14t1.175 2.825T9 18h8v2z",
  search: "m19.6 21l-6.3-6.3q-.75.6-1.725.95T9.5 16q-2.725 0-4.612-1.888T3 9.5t1.888-4.612T9.5 3t4.613 1.888T16 9.5q0 1.1-.35 2.075T14.7 13.3l6.3 6.3zM9.5 14q1.875 0 3.188-1.312T14 9.5t-1.312-3.187T9.5 5T6.313 6.313T5 9.5t1.313 3.188T9.5 14",
  setCover: "M5 21q-.825 0-1.412-.587T3 19V5q0-.825.588-1.412T5 3h14q.825 0 1.413.588T21 5v14q0 .825-.587 1.413T19 21zm0-2h14V5H5zm2-2h10l-3.15-4.2l-2.35 3.1l-1.65-2.2z",
  share: "M18 22q-1.25 0-2.125-.875T15 19q0-.175.025-.363t.075-.337l-7.05-4.1q-.425.375-.95.588T6 15q-1.25 0-2.125-.875T3 12t.875-2.125T6 9q.575 0 1.1.213t.95.587l7.05-4.1q-.05-.15-.075-.337T15 5q0-1.25.875-2.125T18 2t2.125.875T21 5t-.875 2.125T18 8q-.575 0-1.1-.212t-.95-.588L8.9 11.3q.05.15.075.338T9 12t-.025.363t-.075.337l7.05 4.1q.425-.375.95-.587T18 16q1.25 0 2.125.875T21 19t-.875 2.125T18 22",
  moon: "M12 21q-3.75 0-6.375-2.625T3 12q0-3.1 1.875-5.475T9.75 3.25q-.475 1.625-.15 3.313t1.575 2.937t2.938 1.575t3.312-.15q-.9 3-3.275 4.875T12 21m0-2q1.975 0 3.563-1.1T18 15q-2.475.125-4.55-1.675T11.125 8.8q-.35-1.425-.075-2.8q-2.6.8-4.325 3T5 14q0 2.075 1.463 3.538T10 19z",
  swap: "M7 7h11l-3-3l1.4-1.4L21.8 8l-5.4 5.4L15 12l3-3H7zm10 10H6l3 3l-1.4 1.4L2.2 16l5.4-5.4L9 12l-3 3h11z",
  sun: "M12 18q-2.5 0-4.25-1.75T6 12t1.75-4.25T12 6t4.25 1.75T18 12t-1.75 4.25T12 18m0-2q1.65 0 2.825-1.175T16 12t-1.175-2.825T12 8T9.175 9.175T8 12t1.175 2.825T12 16M11 4V1h2v3zm0 19v-3h2v3zM4 13H1v-2h3zm19 0h-3v-2h3zM5.65 7.05L3.75 5.15l1.4-1.4l1.9 1.9zm13.2 13.2l-1.9-1.9l1.4-1.4l1.9 1.9zm-.5-13.2l-1.4-1.4l1.9-1.9l1.4 1.4zM5.15 20.25l-1.4-1.4l1.9-1.9l1.4 1.4z",
  thumbDown: "M10 3h9q.825 0 1.413.588T21 5v8q0 .825-.587 1.413T19 15h-3.15l.95 4.55q.15.7-.287 1.075T15.5 21H15L10 15.75zm-2 12H4q-.825 0-1.412-.587T2 13V5q0-.825.588-1.412T4 3h4z",
  thumbUp: "M14 21H5q-.825 0-1.412-.587T3 19v-8q0-.825.588-1.412T5 9h3.15L7.2 4.45q-.15-.7.287-1.075T8.5 3H9l5 5.25zm2-12h4q.825 0 1.413.588T22 11v8q0 .825-.587 1.413T20 21h-4z",
  tune: "M11 21v-6h2v2h8v2h-8v2zm-8-2v-2h6v2zm4-4v-2H3v-2h4V9h2v6zm4-2v-2h10v2zm4-4V3h2v2h4v2h-4v2zM3 7V5h10v2z",
};

export default function FlowIcon(props) {
  const url = iconUrls[props.name];
  if (url) {
    return <span class="ual-flow-icon is-svg-mask" aria-hidden="true" style={{ "--ual-icon-url": `url("${url}")` }} />;
  }
  return (
    <svg class="ual-flow-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24">
      <path fill="currentColor" d={paths[props.name]} />
    </svg>
  );
}
