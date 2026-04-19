  function tmsg(string,duration,disparg,title,modal)
% function tmsg(string,duration,disparg,title,modal)
% Displays a text message for a specified number 
% of seconds. After "duration" elapses, the message box will 
% self-destruct.
% if disparg=='disp'  also echo string to Matlab command window
% If user sets duration = 0, the message box will remain present
% until user closes it.
% With duration zero, the message can be made "modal" by submitting 
% a string 'modal' in last argument. This works for v5 only.
%
% Dick Benson DSP Technology


  w = 400;
  h = 100;
  if (nargin < 4)
     title = ' ';
    if (nargin < 2)
      duration = 3;
    end;
  end;
  if (nargin > 2)
     if (strcmp(disparg,'disp'))
        disp(string);   % also echo to Matlab command window
     end;
  end;
  
  
  hf =figure('position',[200 200 w h],'menu','none','NumberTitle','off',...
             'name',title,'tag','sigmessage');
  if beyondv4
     if duration == 0  & nargin==5 & strcmp(modal,'modal')
        set(hf,'windowstyle','modal');
     end;
  end;
             
  uicontrol('style','text','position',[20,10,w-40,h-20],'string',string,...
            'backgroundcolor',[.5 0 0],'foregroundcolor','white');
if (duration~=0)
  pause(duration);
  hf = findobj('tag','sigmessage');
  % if user has closed it ... program can't close it. RAB 
  if ~isempty(hf)
     close(hf);
  end;   
end
% end function 









