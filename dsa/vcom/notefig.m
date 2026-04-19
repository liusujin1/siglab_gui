function Out1=notefig(Action,In1,Owner)
% function Out1=notefig(Action,In1,Owner)
% Modal dialog for creating notes.
% Routine no longer does "compression", but still has un-compress function,
% which allows pre v2.25 notes to be recovered. 
% 
% Host VI must have 2 actions 
%    1: vxx('get','note_c') ..... gets compressed note data from host VI to notefig
%    2: vxx('set','note_c') ..... store compressed note data in notefig back to host VI
%       search for Cmprssd_Notes in vos.mi for usage if interested
%    Input args:
%        In1 has owner handle on init
%        Owner is a string with the M-file program name (VI) that invoked this modal dialog.
% 
%    If the un-compress action is invoked, the routine will check if In1 is a char array. 
%    If it is, the array is simply returned. If it is not a char array, but a numeric 
%    array, the un-compress algorithm will be executed. 
%    To un-compress string >>  s = notefig('un_compress',compressed)
%    
%    Dick Benson, DSP Technology 
   
   if strcmp(Action,'init')
   
          dialogname=[Owner,' notes'];
          w = 300;   % width
          h = 200;   % height
          pos=get(In1,'position');
          vcol_h;    % color definition indices
          load vi_color;  colors=stored_vi_colors;
       
          % assume beyondv4  
           hf=figure('numbertitle','off','resize','off','menu','none',...
                     'pos',[pos(1:2)+[0,20],w,h],... 
                     'color',colors(FIG_BKc,:),...
                     'name',dialogname,'WindowStyle','Modal');

          set(hf,'userdata',uicontrol('style','edit','units','pixels',...
                                      'BackGroundColor',colors(EDT_BKc,:),...
                                      'ForeGroundColor',colors(EDT_FRc,:),...
                                      'position',[2,25,w-4,h-30],...
                                      'HorizontalAlignment','left',...
                                      'max',6,'string',  notefig('un_compress',eval([Owner,'(''get'',''note_c'')'])) ));
          
          % assume  beyondv4
             s =  'close(gcf); drawnow;';
          
          uicontrol(hf,'str','Close Notes','pos',[2,2,90,20],...
                    'callback',[Owner,'(''set'',''note_c'');',s]);
          
                    
   elseif strcmp(Action,'get_notes')
          %    userdata has  handle to edit uicontrol
          %  Out1=notefig('compress',get(get(gcf,'userdata'),'string'));
             Out1=get(get(gcf,'userdata'),'string');
          
   elseif strcmp(Action,'compress')
          % this action is a nop 
          Out1 = In1;
          disp('notefig.m compress was called. ??')
     
   elseif strcmp(Action,'un_compress')
          if ischar(In1) 
             Out1 = In1;   % nothing to do
          elseif isnumeric(In1)
             % this must be old compressed format
             % transform numerical input array back to a delimeted text vector 
             text_vec=[];
             for i=1:length(In1)
                 r=In1(i);
                 z=[];
                 for k=6:-1:1
                     z(k)=fix((r)/(256^(k-1)));
                     r=r-z(k)*256^(k-1);
                 end;
                 text_vec=[text_vec,setstr(z)];
             end;
             % compressed text vector back to normal text matrix format
             d=findstr('~',text_vec);
             Out1=[text_vec(1:d(1)-1)];
             for i=2:length(d)
                 % start=d(i-1)+1;
                 % stop =d(i)-1;
                 Out1=put_str(i,Out1,text_vec((d(i-1)+1):(d(i)-1)));
             end;

          else
             disp('error in notfig.m, could not deal with argument passed with un-compress action.')

          end;
   elseif strcmp(Action,'init_msg')
       % have a common initial message. 
         Out1 = 'Enter your notes here.';
   else
      disp('unrecognized Action in notefig.m');
   end;
% end notefig function






