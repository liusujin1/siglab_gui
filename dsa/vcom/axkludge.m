  function axkludge(fig_handle)
% function axkludge(fig_handle)
% A workaround to get V5 axis visibility correct
% on application invocation.
% Dick Benson, DSP Technology 
   if nargin ==0
       fig_handle=gcf;
   end;
   ha = findobj(fig_handle,'type','axes','visible','on');
   set(ha,'visible','off');
   drawnow;
   set(ha,'visible','on');
% end function    




